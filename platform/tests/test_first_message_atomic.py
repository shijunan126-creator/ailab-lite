"""A new conversation exists only after its first turn has passed admission."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ailab_platform.api.main import app
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, Run
from tests.test_conversations import AUTH


def test_rejected_first_message_leaves_no_empty_conversation(monkeypatch) -> None:
    key = f"rejected-{uuid.uuid4().hex}"
    with TestClient(app) as api:
        monkeypatch.setattr(settings, "max_active_runs_per_user", 0)
        with SessionLocal() as db:
            conversations = db.scalar(select(func.count()).select_from(Conversation))

        response = api.post(
            "/v1/conversations/start",
            headers=AUTH,
            json={"prompt": "third while two are running", "client_send_id": key},
        )

        assert response.status_code == 429, response.text
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Conversation)) == conversations
            assert db.scalar(select(func.count()).select_from(Run).where(Run.client_send_id == key)) == 0


def test_first_message_creation_and_retry_use_one_conversation_and_run() -> None:
    key = f"first-{uuid.uuid4().hex}"
    body = {"prompt": "first turn", "client_send_id": key, "mode": "chat"}
    with TestClient(app) as api:
        first = api.post("/v1/conversations/start", headers=AUTH, json=body)
        assert first.status_code == 201, first.text
        started = first.json()
        conversation_id = started["conversation"]["id"]
        run_id = started["run"]["id"]
        assert started["run"]["conversation_id"] == conversation_id

        detail = api.get(f"/v1/conversations/{conversation_id}", headers=AUTH)
        assert detail.status_code == 200
        assert [turn["id"] for turn in detail.json()["turns"]] == [run_id]

        replay = api.post("/v1/conversations/start", headers=AUTH, json=body)
        assert replay.status_code == 201, replay.text
        assert replay.json()["conversation"]["id"] == conversation_id
        assert replay.json()["run"]["id"] == run_id

        changed = api.post("/v1/conversations/start", headers=AUTH, json={**body, "prompt": "different"})
        assert changed.status_code == 409

        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Run).where(Run.client_send_id == key)) == 1
            assert db.scalar(select(func.count()).select_from(Conversation).where(Conversation.id == conversation_id)) == 1
