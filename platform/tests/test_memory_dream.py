"""Nightly memory dream: validation, append-only application and rollback."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.contracts.memory import DreamPlan
from ailab_platform.db import SessionLocal
from ailab_platform.llm import LLMResponse
from ailab_platform.memory_dream import service
from ailab_platform.models import MemoryDreamRun, MemoryEpisode, Run, RunEvent, User
from tests.memory_fixtures import install_plan_builder, make_dream

from .test_skill_settings import _user


def test_dream_plan_is_append_only_and_requires_an_invalidation_target() -> None:
    plan = DreamPlan.model_validate({"changes": [{"op": "invalidate", "id": "mem_a", "reason": "explicit replacement"}]})
    assert plan.changes[0].op == "invalidate"
    with pytest.raises(ValueError):
        DreamPlan.model_validate({"changes": [{"op": "invalidate", "reason": "missing id"}]})


def test_memory_routes_expose_dream_settings_and_history() -> None:
    with TestClient(app) as c:
        me = _user(c)
        me_doc = c.get("/v1/me", headers=me).json()
        doc = c.get("/v1/memory", headers=me).json()
        assert doc["dream"]["enabled"] is True
        patched = c.patch("/v1/memory/dream", headers=me, json={**doc["dream"], "enabled": False})
        assert patched.status_code == 200 and patched.json()["enabled"] is False
        assert c.get("/v1/memory/dreams", headers=me).json() == []

        entry = c.put(
            "/v1/memory",
            headers=me,
            json={"expected_revision": 0, "enabled": True, "auto_save": True, "entries": [{"kind": "user", "content": "旧记忆"}]},
        ).json()["entries"][0]
        with SessionLocal() as db:
            run = make_dream(
                user_id=me_doc["id"],
                local_date="2026-10-26",
                timezone="Asia/Shanghai",
                status="applied",
                trigger="cron",
                input_run_ids=[],
                plan={"changes": [{"op": "add", "kind": "user", "content": "新记忆", "confidence": 0.9, "evidence_run_ids": []}]},
                journal="ok",
                before_memory={"entries": [entry], "enabled": True, "auto_save": True},
                after_memory={"revision": 1, "entries": [entry], "enabled": True, "auto_save": True},
                token_usage={"prompt_tokens": 10, "completion_tokens": 2},
                created_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
            )
            db.add(run)
            db.commit()
            run_id = run.id
        listed = c.get("/v1/memory/dreams", headers=me).json()
        assert listed[0]["id"] == run_id and listed[0]["changes"][0]["content"] == "新记忆"
        detail = c.get(f"/v1/memory/dreams/{run_id}", headers=me).json()
        assert detail["before"][0]["content"] == "旧记忆"
        reverted = c.post(f"/v1/memory/dreams/{run_id}/revert", headers=me)
        assert reverted.status_code == 200 and reverted.json()["status"] == "reverted"
        assert c.get("/v1/memory", headers=me).json()["entries"][0]["content"] == "旧记忆"


def test_manual_dream_ignores_nightly_switch_and_checks_memory_settings():
    with TestClient(app) as c:
        me = _user(c)
        doc = c.get("/v1/memory", headers=me).json()
        assert c.patch("/v1/memory/dream", headers=me, json={**doc["dream"], "enabled": False}).status_code == 200
        assert c.post("/v1/memory/dream/run", headers=me).json()["status"] == "nothing"  # no facts or summary is an empty scope, not stale
        assert c.post("/v1/memory/dream/run", headers=me).json()["status"] == "nothing"
        for enabled, auto_save, detail in [(False, True, "记忆未开启"), (True, False, "自动记忆已关闭，无法整理")]:
            current = c.get("/v1/memory", headers=me).json()
            response = c.put(
                "/v1/memory",
                headers=me,
                json={"expected_revision": current["revision"], "enabled": enabled, "auto_save": auto_save, "entries": []},
            )
            assert response.status_code == 200
            response = c.post("/v1/memory/dream/run", headers=me)
            if enabled:
                assert response.status_code == 200 and response.json()["status"] == "nothing"
            else:
                assert response.status_code == 409 and response.json()["detail"] == detail


def test_invalidate_keeps_history_and_hides_the_old_fact(monkeypatch):
    from ailab_platform.api.routes_memory import memory_for
    from ailab_platform.memory_dream.document import apply_ops
    from ailab_platform.memory_dream.maintenance import save
    from ailab_platform.models import MemoryItem
    from tests.test_memory_document import episode
    from tests.test_memory_hardening import put

    with TestClient(app) as c:
        me = _user(c)
        uid = c.get("/v1/me", headers=me).json()["id"]
        old = put(c, me, [{"content": "用户用Neovim", "origin": "inferred"}])["entries"][0]
        ep = episode()
        ep.run_id = __import__("tests.test_memory_recall", fromlist=["_seed"])._seed(uid, "我住在上海")
        with SessionLocal() as db:
            user = db.get(User, uid)
            before = memory_for(user)
            entries, _audit = apply_ops(
                before["entries"],
                before["entries"],
                [ep],
                [{"op": "update", "id": old["id"], "content": "用户住在上海", "quote": "我住在上海", "run": ep.run_id}],
                user,
                None,
            )
            after = save(db, user, before, entries)
            db.commit()
            assert len(after["entries"]) == 1 and after["entries"][0]["content"] == "用户住在上海"
            row = db.get(MemoryItem, old["id"])
            assert row.status == "closed" and row.valid_to and row.superseded_by


def test_process_dream_applies_without_a_visible_run(monkeypatch) -> None:
    with TestClient(app) as c:
        me = _user(c)
        me_doc = c.get("/v1/me", headers=me).json()
        with SessionLocal() as db:
            user = db.get(User, me_doc["id"])
            for index in range(2):
                db.add(
                    Run(
                        id=f"run_dream_{index}_{user.id}",
                        user_id=user.id,
                        title="t",
                        prompt="以后周报先结论后风险",
                        status="succeeded",
                        summary="好的",
                        created_at=datetime.now(UTC) - timedelta(minutes=5 - index),
                    )
                )
            db.add(
                make_dream(
                    id="dream_test",
                    user_id=user.id,
                    local_date="2026-10-25",
                    timezone="Asia/Shanghai",
                    status="pending",
                    trigger="cron",
                    input_run_ids=[],
                    before_memory={},
                    after_memory=__import__("ailab_platform.api.routes_memory", fromlist=["memory_for"]).memory_for(user),
                    token_usage={},
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()

        async def fake_extract(episodes, active, user):
            episode = episodes[0]
            return (
                [
                    {
                        "text": "周报先结论后风险",
                        "kind": "user",
                        "entities": ["周报"],
                        "evidence_run_ids": [episode.run_id],
                        "evidence_episode_ids": [episode.id],
                        "source_quote": "以后周报先结论后风险",
                        "confidence": 0.95,
                    }
                ],
                {"prompt_tokens": 20, "completion_tokens": 5},
                "fake",
            )

        async def fake_judge(facts, active, user, model):
            return {}, {"prompt_tokens": 0, "completion_tokens": 0}

        install_plan_builder(monkeypatch, fake_extract)
        c.portal.call(service.process_dream, "dream_test")
        with SessionLocal() as db:
            run = db.get(MemoryDreamRun, "dream_test")
            assert run.status == "applied"
            assert db.query(RunEvent).filter(RunEvent.run_id.like("run_dream_%")).count() == 0
        assert any(e["content"] == "周报先结论后风险" for e in c.get("/v1/memory", headers=me).json()["entries"])


def test_dream_chat_bounds_its_output_tokens(monkeypatch) -> None:
    captured: dict = {}

    class Stub:
        async def chat(self, messages, tools, model, user_id=None, *, max_tokens=None):
            captured["max_tokens"] = max_tokens
            return LLMResponse(content='{"facts": []}', usage={})

    monkeypatch.setattr(service, "llm_for", lambda model: Stub())
    asyncio.run(service._chat([{"role": "user", "content": "x"}], "stub", User(id="u_dream")))
    assert captured["max_tokens"] == service.settings.memory_dream_max_tokens


def test_extract_prompt_keeps_an_oldest_prefix_to_fit_the_budget() -> None:
    episodes = [
        MemoryEpisode(
            id=f"epi_{i}",
            run_id=f"run_{i}",
            observed_at=datetime.now(UTC) - timedelta(minutes=10 - i),
            content_hash=f"h{i}",
            digest="x" * 400,
            token_count=100,
        )
        for i in range(6)
    ]
    kept = service._fit_episodes(episodes, 1200)
    assert kept and kept[0].id == episodes[0].id
    assert kept[-1].id != episodes[-1].id  # newer work remains eligible, never skipped by a cursor
    assert len(service._episode_payload(kept)) <= 1200


def test_reverting_a_dream_makes_the_invalidated_fact_visible_again(monkeypatch):
    from tests.test_memory_hardening import put
    from tests.test_memory_recall import _seed, _uid

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        old = put(c, me, [{"content": "旧状态", "origin": "inferred"}])["entries"][0]
        rid = _seed(uid, "新的状态", days_ago=0)

        async def chat(*args):
            return json.dumps({"ops": [{"op": "update", "id": old["id"], "content": "新状态", "run": rid, "quote": "新的状态"}]}), {}

        import json

        monkeypatch.setattr(service, "_chat", chat)
        with SessionLocal() as db:
            row = make_dream(user_id=uid, local_date="2026-01-01", timezone="UTC", status="pending", trigger="manual")
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream_chain(did))
        assert c.get("/v1/memory", headers=me).json()["entries"][0]["content"] == "新状态"
        assert c.post(f"/v1/memory/dreams/{did}/revert", headers=me).status_code == 200
        assert c.get("/v1/memory", headers=me).json()["entries"][0]["content"] == "旧状态"


def test_a_failed_dream_is_retried_the_same_day(monkeypatch) -> None:
    monkeypatch.setattr("ailab_platform.memory_dream.scheduling.offset", lambda *args: 0)
    with TestClient(app) as c:
        me = _user(c)
        me_doc = c.get("/v1/me", headers=me).json()
        monkeypatch.setattr(service, "_candidate_users", lambda db, now: [db.get(User, me_doc["id"])])
        with SessionLocal() as db:
            user = db.get(User, me_doc["id"])
            for index in range(2):
                db.add(
                    Run(
                        id=f"run_retry_{index}_{user.id}",
                        user_id=user.id,
                        title="t",
                        prompt="p",
                        status="succeeded",
                        summary="s",
                        created_at=datetime.now(UTC) - timedelta(minutes=30 - index),
                    )
                )
            db.add(
                make_dream(
                    id="dream_failed",
                    user_id=user.id,
                    local_date="2026-10-26",
                    timezone="Asia/Shanghai",
                    status="failed",
                    trigger="cron",
                    input_run_ids=[],
                    before_memory={},
                    after_memory={},
                    token_usage={},
                    error="boom",
                    created_at=datetime.now(UTC) - timedelta(hours=1),
                )
            )
            db.commit()
        # 19:00 UTC is 03:00 in Shanghai on 2026-10-26: inside the default quiet window.
        now = datetime(2026, 10, 25, 19, 0, tzinfo=UTC)
        claimed = service.claim_due(now=now)
        assert len(claimed) == 1 and claimed[0] != "dream_failed"
        with SessionLocal() as db:
            row = db.get(MemoryDreamRun, claimed[0])
            assert row.status == "pending" and row.trigger == "cron"
            assert len(row.input_run_ids) == 2
        assert service.claim_due(now=now) == []


def test_manual_trigger_allows_new_batches_same_day() -> None:
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
    with TestClient(app) as c:
        me = _user(c)
        me_doc = c.get("/v1/me", headers=me).json()
        with SessionLocal() as db:
            db.add(
                make_dream(
                    id="dream_manual",
                    user_id=me_doc["id"],
                    local_date=today,
                    timezone="Asia/Shanghai",
                    status="failed",
                    trigger="manual",
                    input_run_ids=[],
                    before_memory={},
                    after_memory={},
                    token_usage={},
                    error="x",
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()
        c.post("/v1/memory/dream/run", headers=me)  # materialize the empty account summary
        empty = c.post("/v1/memory/dream/run", headers=me)
        assert empty.status_code == 200 and empty.json() == {"id": None, "status": "nothing", "reused": False, "pending_runs": 0}
        with SessionLocal() as db:
            db.add(Run(id="run_manual_pending", user_id=me_doc["id"], title="t", prompt="p", status="succeeded", summary="s"))
            db.commit()
        started = c.post("/v1/memory/dream/run", headers=me)
        assert started.status_code == 202 and started.json()["id"] != "dream_manual"
        assert started.json()["pending_runs"] == 1


def test_status_and_manual_reuse_active_even_with_unfinished_turn() -> None:
    with TestClient(app) as c:
        me = _user(c)
        uid = c.get("/v1/me", headers=me).json()["id"]
        with SessionLocal() as db:
            db.add(Run(id="run_status_pending", user_id=uid, title="t", prompt="p", status="succeeded", summary="s"))
            db.add(Run(id="run_status_active", user_id=uid, title="t", prompt="p", status="waiting_user", summary="s"))
            db.add(
                MemoryDreamRun(
                    id="dream_active_status",
                    user_id=uid,
                    local_date="2026-10-30",
                    timezone="Asia/Shanghai",
                    status="pending",
                    trigger="manual",
                )
            )
            db.commit()
        response = c.post("/v1/memory/dream/run", headers=me)
        assert response.status_code == 200
        assert response.json() == {"id": "dream_active_status", "status": "pending", "reused": True, "pending_runs": 1}
        status = c.get("/v1/memory/dream/status", headers=me).json()
        assert status["pending_runs"] == 1
        assert status["active"] == {"id": "dream_active_status", "status": "pending", "trigger": "manual"}
        assert status["manual_batches_limit"] == service.settings.memory_dream_manual_batches


def test_manual_chain_respects_limit_after_recovery(monkeypatch):
    from ailab_platform.models import MemoryWorkItem

    monkeypatch.setattr(service.settings, "memory_dream_manual_batches", 3)
    calls = []

    def finish_sync(run_id):
        calls.append(run_id)
        with SessionLocal() as db:
            row = db.get(MemoryDreamRun, run_id)
            pending = service._new_runs(db, row.user_id, None, datetime.now(UTC), limit=1)
            if pending:
                db.add(MemoryWorkItem(run_id=pending[0].id, user_id=row.user_id, state="done", candidates=[]))
            row.status = "applied"
            db.commit()

    async def finish_one(run_id):
        await asyncio.to_thread(finish_sync, run_id)

    monkeypatch.setattr(service, "process_dream", finish_one)
    with TestClient(app) as c:
        uid = c.get("/v1/me", headers=_user(c)).json()["id"]
        with SessionLocal() as db:
            for index in range(4):
                db.add(Run(id=f"run_chain_{index}", user_id=uid, title="t", prompt="p", status="succeeded"))
            db.add(
                MemoryDreamRun(
                    id="dream_chain_resume",
                    user_id=uid,
                    local_date="2026-10-30",
                    timezone="Asia/Shanghai",
                    status="pending",
                    trigger="manual",
                    batch_index=2,
                )
            )
            db.commit()
        c.portal.call(service.process_dream_chain, "dream_chain_resume")
        assert len(calls) == 2
        with SessionLocal() as db:
            assert len(service._new_runs(db, uid, None, datetime.now(UTC))) == 2
