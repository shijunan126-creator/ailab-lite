"""User state is bounded, owner-only and separate from agent-owned results."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ailab_platform.api.main import app
from ailab_platform.api.routes_runs import ui_state_throttle
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, Run, utcnow
from tests.test_conversations import AUTH, _finish, _other_user

STATE = {"v": 1, "blocks": {"0": {"tab": 2, "field": {"value": [None, True, 1.5, "中文"], "componentType": "Select"}}}}


@pytest.fixture
def state_run():
    ui_state_throttle._failures.clear()
    with TestClient(app) as client:
        conv = client.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
        run = client.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "state"}).json()["id"]
        yield client, conv, run, f"/v1/runs/{run}/ui-state"
    ui_state_throttle._failures.clear()


def test_roundtrip_clear_and_result_isolation(state_run):
    client, conv, run, url = state_run
    assert client.get(f"/v1/runs/{run}", headers=AUTH).json()["ui_state"] is None
    assert client.put(url, headers=AUTH, json=STATE).status_code == 204
    _finish(client, run, "done", preserved="agent result")
    assert client.get(f"/v1/runs/{run}", headers=AUTH).json()["ui_state"] == STATE
    assert client.get(f"/v1/conversations/{conv}", headers=AUTH).json()["turns"][0]["ui_state"] == STATE
    assert next(r for r in client.get("/v1/runs", headers=AUTH).json() if r["id"] == run)["ui_state"] == STATE
    replacement = {"v": 1, "blocks": {"1": {"toggle": False}}}
    assert client.put(url, headers=AUTH, json=replacement).status_code == 204
    detail = client.get(f"/v1/runs/{run}", headers=AUTH).json()
    assert detail["ui_state"] == replacement and detail["result"]["preserved"] == "agent result"
    assert client.put(url, headers=AUTH, json={"v": 1, "blocks": {}}).status_code == 204
    with SessionLocal() as db:
        assert db.scalar(text("SELECT ui_state IS NULL FROM runs WHERE id=:id"), {"id": run})


def test_owner_and_deleted_conversation(state_run):
    client, conv, _, url = state_run
    other = _other_user(client)
    assert client.put(url, headers=other, json=STATE).status_code == 404
    assert client.put(url, json=STATE).status_code == 401
    with SessionLocal() as db:
        db.get(Conversation, conv).deleted_at = utcnow()
        db.commit()
    assert client.put(url, headers=AUTH, json=STATE).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"v": 2, "blocks": {}},
        {"v": True, "blocks": {}},
        *({"v": 1, "blocks": {key: {}}} for key in ["-1", "01", "x", "8"]),
        {"v": 1, "blocks": {"0": {str(i): i for i in range(65)}}},
        *(
            {"v": 1, "blocks": {"0": {"x": value}}}
            for value in [
                {"nested": {}},
                [1],
                {"value": {"nested": 1}, "componentType": None},
                {"value": [[1]], "componentType": None},
                {"value": [1] * 51, "componentType": None},
                {"value": 1, "componentType": "x" * 41},
                {"value": 1, "componentType": None, "extra": 1},
                "x" * 1001,
            ]
        ),
        *({"v": 1, "blocks": {"0": {key: 1}}} for key in ["", "x" * 121]),
        {"v": 1, "blocks": {"0": {str(i): "中" * 1000 for i in range(6)}}},
    ],
)
def test_invalid_state(state_run, body):
    client, _, _, url = state_run
    assert client.put(url, headers=AUTH, json=body).status_code == 422


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("wrapped", [False, True])
def test_nonfinite_state(state_run, value, wrapped):
    client, _, _, url = state_run
    body = {"v": 1, "blocks": {"0": {"x": {"value": [value], "componentType": None} if wrapped else value}}}
    response = client.put(url, headers={**AUTH, "Content-Type": "application/json"}, content=json.dumps(body))
    assert response.status_code == 422


def test_block_limit_is_configured(state_run, monkeypatch):
    client, _, _, url = state_run
    monkeypatch.setattr(settings, "genui_max_blocks", 2)
    assert client.put(url, headers=AUTH, json={"v": 1, "blocks": {"2": {}}}).status_code == 422


def test_fork_and_share(state_run):
    client, conv, run, url = state_run
    _finish(client, run, "answer")
    assert client.put(url, headers=AUTH, json=STATE).status_code == 204
    fork = client.post(f"/v1/conversations/{conv}/fork", headers=AUTH, json={"run_id": run, "mode": "work"})
    assert fork.status_code == 201, fork.text
    copied = client.get(f"/v1/conversations/{fork.json()['id']}", headers=AUTH).json()["turns"][0]
    assert copied["ui_state"] is None
    assert client.put(f"/v1/runs/{copied['id']}/ui-state", headers=AUTH, json=STATE).status_code == 204
    share = client.post("/v1/shares", headers=AUTH, json={"kind": "conversation", "conversation_id": conv, "audience": "everyone"})
    assert share.status_code == 201, share.text
    viewer = _other_user(client)
    view = client.get(f"/v1/shares/{share.json()['id']}/view", headers=viewer)
    assert view.status_code == 200, view.text
    assert view.json()["turns"][0]["ui_state"] == STATE
    assert client.put(url, headers=viewer, json=STATE).status_code == 404


def test_throttle(state_run):
    client, _, _, url = state_run
    for _ in range(120):
        assert client.put(url, headers=AUTH, json=STATE).status_code == 204
    response = client.put(url, headers=AUTH, json=STATE)
    assert response.status_code == 429
    assert 0 < int(response.headers["Retry-After"]) <= 61


@pytest.mark.parametrize("status", ["queued", "running", "interrupted", "succeeded", "failed", "canceled"])
def test_any_run_status(state_run, status):
    client, _, run, url = state_run
    with SessionLocal() as db:
        db.get(Run, run).status = status
        db.commit()
    assert client.put(url, headers=AUTH, json=STATE).status_code == 204


def test_exact_byte_limit_and_scalar_boundaries(state_run):
    client, _, _, url = state_run
    entries = {str(i): "x" * 1000 for i in range(16)}
    body = {"v": 1, "blocks": {"0": entries}}
    entries["pad"] = ""
    size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())
    entries["pad"] = "x" * (16384 - size)
    assert len(json.dumps(body, separators=(",", ":")).encode()) == 16384
    assert client.put(url, headers=AUTH, json=body).status_code == 204
    entries["pad"] += "x"
    assert client.put(url, headers=AUTH, json=body).status_code == 422
    entries = {str(i): None for i in range(63)}
    entries["x" * 120] = {"value": ["中" * 1000] + [False] * 49, "componentType": "x" * 40}
    assert client.put(url, headers=AUTH, json={"v": 1, "blocks": {"0": entries}}).status_code == 204
