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
        asyncio.run(service.process_dream("dream_test"))
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
        assert "dream_failed" in service.claim_due(now=now)
        with SessionLocal() as db:
            row = db.get(MemoryDreamRun, "dream_failed")
            assert row.status == "pending" and row.trigger == "cron" and row.error == ""
            assert len(row.input_run_ids) == 2  # the retry also picks up runs since the failure
            row.status = "applied"
            db.commit()
        assert "dream_failed" not in service.claim_due(now=now)


def test_manual_trigger_retries_a_failed_night_and_refuses_an_applied_one() -> None:
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
        retried = c.post("/v1/memory/dream/run", headers=me)
        assert retried.status_code == 200 and retried.json()["id"] == "dream_manual"
        with SessionLocal() as db:
            row = db.get(MemoryDreamRun, "dream_manual")
            row.status = "applied"
            db.commit()
        assert c.post("/v1/memory/dream/run", headers=me).status_code == 409
