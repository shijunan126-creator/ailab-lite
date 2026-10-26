"""Nightly memory dream: validation, append-only application and rollback."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.contracts.memory import DreamPlan
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.models import MemoryDreamRun, Run, RunEvent, User

from .test_skill_settings import _user


def test_dream_plan_validation_drops_unsafe_or_unknown_changes() -> None:
    plan = DreamPlan.model_validate(
        {
            "changes": [
                {"op": "add", "kind": "user", "content": "喜欢简洁", "confidence": 0.9, "evidence_run_ids": ["run_1"]},
                {"op": "add", "kind": "user", "content": "喜欢简洁", "confidence": 0.9, "evidence_run_ids": ["run_1"]},
                {"op": "update", "id": "mem_missing", "content": "x", "confidence": 0.9},
                {"op": "add", "kind": "memory", "content": "低置信", "confidence": 0.2, "evidence_run_ids": ["run_1"]},
            ]
        }
    )
    memory = {"entries": [{"id": "mem_a", "kind": "user", "content": "旧", "superseded_by": None}]}
    got = service._validate(plan, memory, {"run_1"})
    assert [(c.op, c.content) for c in got.changes] == [("add", "喜欢简洁")]


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

        async def fake_model(memory, runs, user):
            return DreamPlan.model_validate(
                {
                    "changes": [
                        {"op": "add", "kind": "user", "content": "周报先结论后风险", "confidence": 0.95, "evidence_run_ids": [runs[0].id]}
                    ],
                    "journal": "整理周报偏好",
                }
            ), {"prompt_tokens": 20, "completion_tokens": 5}

        monkeypatch.setattr(service, "_call_model", fake_model)
        monkeypatch.setattr(service.settings, "memory_dream_min_runs", 1)
        asyncio.run(service.process_dream("dream_test"))
        with SessionLocal() as db:
            run = db.get(MemoryDreamRun, "dream_test")
            assert run.status == "applied"
            assert db.query(RunEvent).filter(RunEvent.run_id.like("run_dream_%")).count() == 0
        assert any(e["content"] == "周报先结论后风险" for e in c.get("/v1/memory", headers=me).json()["entries"])
