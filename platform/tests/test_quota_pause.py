"""Quota refusal is a resumable business pause, before model retries or delivery."""

import asyncio
import json

import pytest
from sqlalchemy import select

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.agent.budgets import BudgetPause
from ailab_platform.agent.loop import AgentLoop
from ailab_platform.catalog import Catalog, ModelSpec
from ailab_platform.clients import ClientError
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.llm import LLMResponse
from ailab_platform.models import Run, RunEvent
from tests.faults.test_long_run_recovery import platform_client
from tests.faults.test_transactional_jobs import api  # noqa: F401
from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM
from tests.test_inbox_queue import conversation, enqueue, finish
from tests.test_ledger_protocol import AUTH
from tests.test_runtime_protocol import owned, save
from tests.test_transactional_jobs import claim

PAUSE = {"kind": "quota", "window": "daily_tokens", "resets_at": "2026-10-26T00:00:00+00:00"}
SOURCES = [{"title": "参考", "url": "https://example.com/", "site": "example.com"}]
PLAN = [{"id": "1", "title": "生成文件", "status": "done"}]
ARTIFACTS = [{"file_id": "confirmed", "filename": "a.txt", "size": 1}]
PREVIEW = {"filename": "a.txt", "file_id": "preview"}


class SnapshotBroker(FakeBroker):
    def __init__(self, rid):
        super().__init__()
        self.rid = rid

    async def snapshot(self, lease_id):
        self.snapshots += 1
        return {"key": f"workspaces/{self.rid}/saved.tgz", "size": 1}


class Broker(SnapshotBroker):
    async def refresh_previews(self, lease_id):
        return [PREVIEW]

    async def list_files(self, *args):
        return [{"path": "/workspace/outputs/extra.txt", "size": 1}]

    async def commit(self, *args, **kwargs):
        raise AssertionError("quota pause must not deliver extra files")


def seeded(api):  # noqa: F811
    rid, token, headers = owned(api)
    state = fresh_state()
    state.update(
        messages=[{"role": "user", "content": "继续完成任务"}],
        sources=SOURCES,
        plan=PLAN,
        artifacts=ARTIFACTS,
        workspace={"key": f"workspaces/{rid}/old.tgz", "size": 1},
        workspace_dirty=False,
    )
    assert save(api, rid, headers, state=state).status_code == 200
    return rid, token, headers


def process(rid, token, model, broker=None):
    async def run():
        platform = await platform_client()
        try:
            await AgentLoop(platform, broker or SnapshotBroker(rid), model).process(rid, attempt_id=token)
        finally:
            await platform.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("window", ["daily_tokens", "monthly_tokens"])
def test_mid_task_quota_retains_work_and_resume_rechecks_then_clears(api, monkeypatch, window):  # noqa: F811
    rid, token, _ = seeded(api)
    setting = "daily_token_limit" if window == "daily_tokens" else "monthly_token_limit"
    monkeypatch.setattr(settings, setting, 0)
    model, broker = ScriptedLLM([]), Broker(rid)
    process(rid, token, model, broker)
    detail = api.get(f"/v1/runs/{rid}", headers=AUTH).json()
    assert detail["status"] == "interrupted" and detail["resumable"]
    reason = ("今日" if window == "daily_tokens" else "本月") + "额度已用完，已暂停，额度恢复后可继续"
    assert detail["error"] == reason
    result = detail["result"]
    assert result["pause"]["kind"] == "quota" and result["pause"]["window"] == window
    assert result["pause"]["resets_at"].endswith("+00:00")
    assert result["sources"] == SOURCES and result["plan"] == PLAN and result["artifacts"] == ARTIFACTS
    assert result["previews"] == [PREVIEW] and result["workspace_saved"] and broker.snapshots
    assert model.seen_messages == []
    with SessionLocal() as db:
        assert db.get(Run, rid).runtime_meta.get("job_id", rid) == rid  # no automatic continuation
        budgets = db.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == "agent.budget")).all()
        assert len(budgets) == 1
        assert budgets[0].payload == {"kind": "paused", "reason": reason, "minutes": 0.0}
    refused = api.post(f"/v1/runs/{rid}/resume", headers=AUTH)
    assert refused.status_code == 429
    assert refused.json()["detail"] == {"code": "quota_exceeded", "window": window, "resets_at": result["pause"]["resets_at"]}
    still = api.get(f"/v1/runs/{rid}", headers=AUTH).json()
    assert still["status"] == "interrupted" and still["resumable"] and still["result"] == result
    monkeypatch.setattr(settings, setting, 100_000_000)
    resumed = api.post(f"/v1/runs/{rid}/resume", headers=AUTH)
    assert resumed.status_code == 200 and "pause" not in resumed.json()["result"]
    with SessionLocal() as db:
        job = db.get(Run, rid).runtime_meta["job_id"]
    process(rid, claim(api, job)["token"], ScriptedLLM([LLMResponse(content="完成")]))
    done = api.get(f"/v1/runs/{rid}", headers=AUTH).json()
    assert done["status"] == "succeeded" and not done["result"].get("pause")


@pytest.mark.parametrize("resume_first", [False, True])
def test_nonquota_pause_removes_stale_quota_metadata(api, resume_first):  # noqa: F811
    rid, _, headers = seeded(api)
    payload = {"status": "interrupted", "result": {"pause": PAUSE, "sources": SOURCES}}
    if resume_first:
        assert api.post(f"/internal/runs/{rid}/pause", headers=headers, json=payload).status_code == 200
        assert api.post(f"/v1/runs/{rid}/resume", headers=AUTH).status_code == 200
        with SessionLocal() as db:
            job = db.get(Run, rid).runtime_meta["job_id"]
        token = claim(api, job)["token"]
        # A resumed budget pause uses the real Agent saving path.

        class Model(ScriptedLLM):
            async def chat(self, *args, **kwargs):
                raise BudgetPause("工作预算已用完，已暂停，可继续")

        process(rid, token, Model([]))
    else:
        # Also cover an API caller omitting result entirely, without a resume clearing it first.
        with SessionLocal() as db:
            db.get(Run, rid).result = payload["result"]
            db.commit()
        assert api.post(f"/internal/runs/{rid}/pause", headers=headers, json={"status": "interrupted"}).status_code == 200
    detail = api.get(f"/v1/runs/{rid}", headers=AUTH).json()
    assert detail["status"] == "interrupted" and detail["resumable"] and not detail["result"].get("pause")
    assert detail["result"]["sources"] == SOURCES


def test_reservation_quota_bypasses_model_retry_fallback_and_failure_metrics(monkeypatch):
    from ailab_platform import metrics

    catalog = Catalog([ModelSpec("m", "m", provider="mock", fallback=["fallback"]), ModelSpec("fallback", "f", provider="mock")])
    monkeypatch.setattr("ailab_platform.agent.loop.get_catalog", lambda: catalog)
    calls = []

    class Platform(FakePlatform):
        async def reserve_usage(self, payload):
            calls.append(payload)
            raise ClientError(
                "POST",
                "/internal/usage/reserve",
                429,
                json.dumps({"detail": {"code": "quota_exceeded", **{k: PAUSE[k] for k in ("window", "resets_at")}}}),
            )

    platform, model = Platform(), ScriptedLLM([])
    loop = AgentLoop(platform, FakeBroker(), model)
    before = list(metrics.LLM_CALLS.collect()[0].samples)
    with pytest.raises(BudgetPause):
        asyncio.run(loop._call_model(model, "r", {"_attempt_id": "a"}, "m", [], 0))
    assert len(calls) == 1 and not model.seen_messages and not platform.events
    assert list(metrics.LLM_CALLS.collect()[0].samples) == before


def test_admission_messages_and_manual_queue_resume_keep_structured_quota(api, monkeypatch):  # noqa: F811
    conv, rid, headers = conversation(api)
    assert enqueue(api, conv).status_code == 201
    assert finish(api, rid, headers, "canceled").status_code == 200
    monkeypatch.setattr(settings, "daily_token_limit", 0)
    for path, body in (
        (f"/v1/conversations/{conv}/messages", {"prompt": "new", "model": "test-fast"}),
        (f"/v1/conversations/{conv}/queue/resume", None),
    ):
        response = api.post(path, headers=AUTH, json=body)
        assert response.status_code == 429, response.text
        detail = response.json()["detail"]
        assert detail["code"] == "quota_exceeded" and detail["window"] == "daily_tokens" and detail["resets_at"]


def test_conservative_reservation_pauses_even_when_admission_has_room(api, monkeypatch):  # noqa: F811
    rid, token, _ = seeded(api)
    usage = api.get("/v1/usage", headers=AUTH).json()["daily_tokens"]
    monkeypatch.setattr(settings, "daily_token_limit", int(usage["used"] + usage["reserved"]) + 1)
    process(rid, token, ScriptedLLM([]), Broker(rid))
    paused = api.get(f"/v1/runs/{rid}", headers=AUTH).json()
    assert paused["status"] == "interrupted" and paused["result"]["pause"]["kind"] == "quota"
    assert api.post(f"/v1/runs/{rid}/resume", headers=AUTH).status_code == 200  # admission is distinct from reservation


def test_monthly_pause_survives_timeout_and_workspace_gc_until_reset(api, monkeypatch, tmp_path):  # noqa: F811
    import os
    from datetime import UTC, datetime, timedelta

    from ailab_platform.api.reaper import expire_interrupted_runs, purge_expired_workspaces
    from ailab_platform.storage import LocalStorage

    rid, _, headers = seeded(api)
    now = datetime.now(UTC)
    reset = now + timedelta(days=25)
    key = f"workspaces/{rid}/old.tgz"
    storage = LocalStorage(tmp_path)
    storage.put_bytes(key, b"workspace")
    old = (now - timedelta(days=4)).timestamp()
    os.utime(tmp_path / key, (old, old))
    metadata = {"kind": "quota", "window": "monthly_tokens", "resets_at": reset.isoformat()}
    assert (
        api.post(f"/internal/runs/{rid}/pause", headers=headers, json={"status": "interrupted", "result": {"pause": metadata}}).status_code
        == 200
    )
    with SessionLocal() as db:
        expire_interrupted_runs(db, now + timedelta(days=5))
        assert db.get(Run, rid).status == "interrupted"
    assert purge_expired_workspaces(storage, now + timedelta(days=5)) == 0
    assert storage.exists(key)
    with SessionLocal() as db:
        expire_interrupted_runs(db, reset + timedelta(hours=settings.ask_user_timeout_hours + 1))
        assert db.get(Run, rid).status == "canceled"
    assert purge_expired_workspaces(storage, reset + timedelta(days=5)) == 1
