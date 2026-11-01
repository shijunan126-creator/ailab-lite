"""Nightly memory dream: validation, append-only application and rollback."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.contracts.memory import DreamChange, DreamPlan
from ailab_platform.db import SessionLocal
from ailab_platform.llm import LLMResponse
from ailab_platform.memory_dream import service
from ailab_platform.models import MemoryDreamRun, MemoryEpisode, Run, RunEvent, User

from .test_skill_settings import _user


def test_dream_plan_is_append_only_and_requires_an_invalidation_target() -> None:
    plan = DreamPlan.model_validate({"changes": [{"op": "invalidate", "id": "mem_a", "reason": "explicit replacement"}]})
    assert plan.changes[0].op == "invalidate"
    with pytest.raises(ValueError):
        DreamPlan.model_validate({"changes": [{"op": "invalidate", "reason": "missing id"}]})


def test_fuzzy_candidate_selection_prefers_related_facts() -> None:
    active = [
        {"id": "mem_a", "content": "用户喜欢简洁的中文回答", "entities": ["中文"]},
        {"id": "mem_b", "content": "用户目前住在上海", "entities": ["上海"]},
    ]
    ids = [c["id"] for c in service._candidates({"text": "用户偏好简洁中文", "entities": []}, active)]
    assert "mem_a" in ids and "mem_b" not in ids


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
            run = MemoryDreamRun(
                user_id=me_doc["id"],
                local_date="2026-10-26",
                timezone="Asia/Shanghai",
                status="applied",
                trigger="cron",
                input_run_ids=[],
                plan={"changes": [{"op": "add", "kind": "user", "content": "新记忆", "confidence": 0.9, "evidence_run_ids": []}]},
                journal="ok",
                before_memory={"entries": [entry], "enabled": True, "auto_save": True},
                after_memory={"entries": [entry], "enabled": True, "auto_save": True},
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


def test_invalidate_keeps_history_and_hides_the_old_fact() -> None:
    with TestClient(app) as c:
        me = _user(c)
        me_doc = c.get("/v1/me", headers=me).json()
        old = c.put(
            "/v1/memory",
            headers=me,
            json={"expected_revision": 0, "enabled": True, "auto_save": True, "entries": [{"kind": "user", "content": "用户用 Neovim"}]},
        ).json()["entries"][0]
        with SessionLocal() as db:
            user = db.get(User, me_doc["id"])
            service._apply(
                db,
                user,
                DreamPlan(
                    changes=[
                        DreamChange(op="add", kind="user", content="用户现在用 VS Code", confidence=0.95),
                        DreamChange(op="invalidate", id=old["id"], reason="explicit replacement"),
                    ]
                ),
                {},
            )
            db.commit()
        entries = c.get("/v1/memory", headers=me).json()["entries"]
        assert len(entries) == 2
        assert next(e for e in entries if e["id"] == old["id"])["invalid_at"]
        assert next(e for e in entries if e["content"] == "用户现在用 VS Code")["invalid_at"] is None


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
                MemoryDreamRun(
                    id="dream_test",
                    user_id=user.id,
                    local_date="2026-10-25",
                    timezone="Asia/Shanghai",
                    status="pending",
                    trigger="cron",
                    input_run_ids=[],
                    before_memory={},
                    after_memory={},
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

        monkeypatch.setattr(service, "_extract", fake_extract)
        monkeypatch.setattr(service, "_judge", fake_judge)
        monkeypatch.setattr(service.settings, "memory_dream_min_runs", 1)
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


def test_extraction_prompt_distinguishes_durable_memory_from_task_requests() -> None:
    from ailab_platform.memory_dream.prompts import EXTRACT_SYSTEM

    assert "an unrelated new conversation weeks later" in EXTRACT_SYSTEM
    assert "Never infer a preference from repeated behaviour" in EXTRACT_SYSTEM
    assert "at least two different conversations" not in EXTRACT_SYSTEM
    assert "换个方式可视化" in EXTRACT_SYSTEM
    assert "empty facts list is normal" in EXTRACT_SYSTEM


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


def test_reverting_a_dream_makes_the_invalidated_fact_visible_again() -> None:
    with TestClient(app) as c:
        me = _user(c)
        me_doc = c.get("/v1/me", headers=me).json()
        old = c.put(
            "/v1/memory",
            headers=me,
            json={"expected_revision": 0, "enabled": True, "auto_save": True, "entries": [{"kind": "user", "content": "用户用 Neovim"}]},
        ).json()["entries"][0]
        before = c.get("/v1/memory", headers=me).json()
        with SessionLocal() as db:
            user = db.get(User, me_doc["id"])
            service._apply(
                db,
                user,
                DreamPlan(
                    changes=[
                        DreamChange(op="add", kind="user", content="用户现在用 VS Code", confidence=0.95),
                        DreamChange(op="invalidate", id=old["id"], reason="explicit replacement"),
                    ]
                ),
                {},
            )
            db.add(
                MemoryDreamRun(
                    id="dream_revert",
                    user_id=user.id,
                    local_date="2026-10-26",
                    timezone="Asia/Shanghai",
                    status="applied",
                    trigger="cron",
                    input_run_ids=[],
                    plan={},
                    journal="",
                    before_memory={"entries": before["entries"], "enabled": before["enabled"], "auto_save": before["auto_save"]},
                    after_memory={},
                    token_usage={},
                    created_at=datetime.now(UTC),
                )
            )
            db.commit()
        assert c.post("/v1/memory/dreams/dream_revert/revert", headers=me).status_code == 200
        entries = c.get("/v1/memory", headers=me).json()["entries"]
        assert next(e for e in entries if e["content"] == "用户用 Neovim")["invalid_at"] is None
        assert all(e["content"] != "用户现在用 VS Code" for e in entries)


def test_a_failed_dream_is_retried_the_same_day(monkeypatch) -> None:
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
                MemoryDreamRun(
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
                MemoryDreamRun(
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


def test_extractor_reports_rejected_candidates_and_does_not_send_existing_facts(monkeypatch):
    import json

    captured = []

    async def chat(messages, model, user):
        captured.extend(messages)
        return json.dumps(
            {
                "facts": [
                    {"text": "用户喜欢蓝色", "source_quote": "我喜欢蓝色", "confidence": 0.1},
                    {"text": "用户喜欢红色", "source_quote": "不存在", "confidence": 0.99},
                ]
            }
        ), {}

    monkeypatch.setattr(service, "_chat", chat)
    episode = MemoryEpisode(
        id="epi_test",
        user_id="u",
        run_id="r",
        observed_at=datetime.now(UTC),
        content_hash="x",
        digest=json.dumps({"user_said": "我喜欢蓝色", "assistant_replied": ""}, ensure_ascii=False),
        token_count=1,
    )
    rejected = []
    token = service.dream_rejected.set(rejected)
    try:
        facts, _, _ = asyncio.run(service._extract([episode], [{"id": "m", "content": "PRIVATE EXISTING FACT"}], User(id="u")))
    finally:
        service.dream_rejected.reset(token)
    assert facts == []
    assert [r["outcome"] for r in rejected] == ["low_confidence", "quote_mismatch"]
    assert "PRIVATE EXISTING FACT" not in captured[1]["content"]
    assert "仅用于理解指代，不是证据" in captured[1]["content"]


def test_judge_sorts_existing_memories_before_new_candidates(monkeypatch):
    import json

    captured = []

    async def chat(messages, model, user):
        captured.append(messages)
        return json.dumps({"results": []}), {}

    monkeypatch.setattr(service, "_chat", chat)
    old = [{"id": "b", "content": "B"}, {"id": "a", "content": "A"}]
    for entries, text in ((old, "first"), (list(reversed(old)), "second")):
        asyncio.run(service._judge([{"text": text, "source_quote": text}], entries, User(id="u"), "m"))
    assert captured[0][0] == captured[1][0]
    assert captured[0][1]["content"].split('"new":')[0] == captured[1][1]["content"].split('"new":')[0]
    assert [entry["id"] for entry in json.loads(captured[0][1]["content"])["existing"]] == ["a", "b"]


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
