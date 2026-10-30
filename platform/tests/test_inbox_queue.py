"""Inbox and queue contracts; shared suite runs against SQLite and PostgreSQL."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from ailab_platform.api.inbox import create_notification
from ailab_platform.db import SessionLocal
from ailab_platform.models import Notification, QueuedMessage, Run, RunEvent, utcnow
from tests.test_ledger_protocol import AUTH, SERVICE, api  # noqa: F401
from tests.test_runtime_protocol import pause, save
from tests.test_transactional_jobs import claim


def conversation(api):  # noqa: F811
    conv = api.post("/v1/conversations", headers=AUTH, json={"title": "通知测试"}).json()["id"]
    run = api.post(
        f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "first", "model": "test-fast", "mode": "chat"}
    ).json()["id"]
    headers = {**SERVICE, "X-Run-Attempt": claim(api, run)["token"]}
    assert api.post(f"/internal/runs/{run}/status", headers=headers, json={"status": "running"}).status_code == 200
    return conv, run, headers


def enqueue(api, conv, **values):  # noqa: F811
    return api.post(
        f"/v1/conversations/{conv}/queue", headers=AUTH, json={"prompt": "next", "model": "test-fast", "mode": "chat", **values}
    )


def finish(api, run, headers, status="succeeded"):  # noqa: F811
    return api.post(f"/internal/runs/{run}/status", headers=headers, json={"status": status})


def detail(api, conv):  # noqa: F811
    return api.get(f"/v1/conversations/{conv}", headers=AUTH).json()


def test_notification_prune_page_read(api):  # noqa: F811
    conv, run, headers = conversation(api)
    with SessionLocal() as db:
        uid = db.get(Run, run).user_id
        for i in range(205):
            create_notification(db, uid, "share.received", str(i), conversation_id=conv)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == uid)) == 200
    first = api.get("/v1/notifications?limit=50", headers=AUTH).json()
    second = api.get("/v1/notifications", params={"limit": 50, "cursor": first["next_cursor"]}, headers=AUTH).json()
    assert len(first["items"]) == 50 and first["unread"] == 200
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
    assert first["items"][0]["created_at"].endswith("Z")
    assert api.post("/v1/notifications/read", headers=AUTH, json={"ids": [first["items"][0]["id"]]}).json() == {"unread": 199}
    assert api.post("/v1/notifications/read", headers=AUTH, json={"conversation_id": conv}).json() == {"unread": 0}
    assert api.get("/v1/notifications/unread", headers=AUTH).json()["latest_at"]
    assert api.post("/v1/notifications/read", headers=AUTH, json={}).status_code == 422
    assert api.get("/v1/notifications?cursor=bad", headers=AUTH).status_code == 422
    finish(api, run, headers)


@pytest.mark.parametrize(
    "status,scheduled,long,kind",
    [
        ("succeeded", False, False, None),
        ("succeeded", False, True, "run.succeeded"),
        ("failed", False, True, "run.failed"),
        ("canceled", True, True, None),
        ("succeeded", True, False, "schedule.succeeded"),
        ("failed", True, False, "schedule.failed"),
    ],
)
def test_terminal_notification(api, status, scheduled, long, kind):  # noqa: F811
    _, run, headers = conversation(api)
    with SessionLocal() as db:
        row = db.get(Run, run)
        if long:
            row.created_at = utcnow() - timedelta(seconds=61)
        if scheduled:
            row.options = {**row.options, "schedule_id": "deleted-task"}
        db.commit()
    assert finish(api, run, headers, status).status_code == 200
    assert finish(api, run, headers, status).status_code == 200
    with SessionLocal() as db:
        assert [r.kind for r in db.scalars(select(Notification).where(Notification.run_id == run))] == ([kind] if kind else [])


def test_wait_queue_and_notification(api):  # noqa: F811
    conv, run, headers = conversation(api)
    assert save(api, run, headers).status_code == 200
    assert pause(api, run, headers).status_code == 200
    assert enqueue(api, conv, steer=True).json()["state"] == "queued"
    assert api.post(f"/internal/runs/{run}/steer/claim", headers=headers).status_code == 409
    with SessionLocal() as db:
        assert (
            db.scalar(select(func.count()).select_from(Notification).where(Notification.run_id == run, Notification.kind == "run.waiting"))
            == 1
        )


def test_queue_retry_with_the_same_send_id_finds_the_queued_message(api):  # noqa: F811
    conv, _, _ = conversation(api)
    first = enqueue(api, conv, client_send_id="queued-once")
    assert first.status_code == 201, first.text
    assert enqueue(api, conv, client_send_id="queued-once").json()["id"] == first.json()["id"]
    assert len(detail(api, conv)["queue"]) == 1
    assert enqueue(api, conv, client_send_id="another").json()["id"] != first.json()["id"]


def test_queue_limits_edit_cancel_delete(api, monkeypatch):  # noqa: F811
    conv, _, _ = conversation(api)
    for values in [{"model": "missing"}, {"file_ids": ["other"]}, {"skill": "missing"}]:
        assert enqueue(api, conv, **values).status_code == 400
    rows = [enqueue(api, conv).json() for _ in range(5)]
    assert enqueue(api, conv).status_code == 422
    qid = rows[0]["id"]
    assert (
        api.patch(f"/v1/conversations/{conv}/queue/{qid}", headers=AUTH, json={"prompt": "edited", "steer": True}).json()["prompt"]
        == "edited"
    )
    assert api.delete(f"/v1/conversations/{conv}/queue/{qid}", headers=AUTH).status_code == 204
    assert len(detail(api, conv)["queue"]) == 4
    assert api.delete(f"/v1/conversations/{conv}", headers=AUTH).status_code == 204
    with SessionLocal() as db:
        assert all(r.state == "canceled" for r in db.scalars(select(QueuedMessage).where(QueuedMessage.conversation_id == conv)))
    from ailab_platform.api.reaper import purge_deleted_conversations
    from ailab_platform.config import settings
    from ailab_platform.models import Conversation

    monkeypatch.setattr(settings, "conversation_purge_days", 1)
    monkeypatch.setattr(settings, "gc_batch_size", 3)
    with SessionLocal() as db:
        db.get(Conversation, conv).deleted_at = utcnow() - timedelta(days=2)
        db.commit()
        for _ in range(20):
            purge_deleted_conversations(db, utcnow())
            if db.get(Conversation, conv, populate_existing=True) is None:
                break
        assert db.get(Conversation, conv, populate_existing=True) is None
        assert db.scalar(select(func.count()).select_from(QueuedMessage).where(QueuedMessage.conversation_id == conv)) == 0


@pytest.mark.parametrize("status", ["failed", "canceled"])
def test_pause_resume_and_edit(api, status):  # noqa: F811
    conv, run, headers = conversation(api)
    qid = enqueue(api, conv, steer=True).json()["id"]
    finish(api, run, headers, status)
    assert detail(api, conv)["queue_paused"] is True
    resumed = api.post(f"/v1/conversations/{conv}/queue/resume", headers=AUTH)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["id"] == qid and resumed.json()["state"] == "started"
    new = detail(api, conv)["active_run_id"]
    with SessionLocal() as db:
        assert db.get(Run, new).parent_run_id == run
    headers = {**SERVICE, "X-Run-Attempt": claim(api, new)["token"]}
    api.post(f"/internal/runs/{new}/status", headers=headers, json={"status": "running"})
    finish(api, new, headers)
    assert enqueue(api, conv).json() == {"detail": {"code": "conversation_idle", "message": "对话空闲，请直接发送"}}
    response = api.post(f"/v1/runs/{new}/edit", headers=AUTH, json={"prompt": "replacement"})
    assert response.status_code == 201, response.text
    assert detail(api, conv)["queue_paused"] is True


def test_steer_checkpoint_event_history(api):  # noqa: F811
    from ailab_platform.adapters.runtime import fresh_state
    from ailab_platform.agent.prompts import history_messages

    conv, run, headers = conversation(api)
    qid = enqueue(api, conv, steer=True, prompt="补充内容").json()["id"]
    assert api.get(f"/internal/runs/{run}/state", headers=headers).json()["steer_pending"] is True
    assert api.post(f"/internal/runs/{run}/steer/claim", headers=headers).json()["items"][0]["id"] == qid
    assert api.get(f"/v1/runs/{run}", headers=AUTH).json()["steered"] == []
    state = fresh_state()
    state.update(messages=[{"role": "user", "content": "补充内容"}], steer_ids=[qid])
    assert save(api, run, headers, state=state).status_code == 200
    assert api.get(f"/v1/runs/{run}", headers=AUTH).json()["steered"] == [{"id": qid, "prompt": "补充内容"}]
    assert detail(api, conv)["turns"][0]["steered"] == [{"id": qid, "prompt": "补充内容"}]
    assert save(api, run, headers, step=1, state=state).status_code == 200
    assert api.post(f"/internal/runs/{run}/steer/claim", headers=headers).json() == {"items": []}
    assert api.get(f"/internal/runs/{run}/state", headers=headers).json()["steer_pending"] is False
    assert save(api, run, headers, step=2).status_code == 422
    with SessionLocal() as db:
        assert len(db.scalars(select(RunEvent).where(RunEvent.run_id == run, RunEvent.type == "agent.steered")).all()) == 1
    finish(api, run, headers)
    new = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "later", "mode": "chat"}).json()["id"]
    history = api.get(f"/internal/runs/{new}", headers=SERVICE).json()["history"]
    assert history[-1]["steers"] == ["补充内容"]
    assert history_messages(history)[1] == {"role": "user", "content": "补充内容"}
    page = api.get(f"/internal/runs/{new}/model-context", headers=SERVICE).json()
    assert page["history"][-1]["steers"] == ["补充内容"]
    assert history_messages(page["history"])[1] == {"role": "user", "content": "补充内容"}
    fork = api.post(f"/v1/conversations/{conv}/fork", headers=AUTH, json={"run_id": run, "mode": "chat"})
    assert fork.status_code == 201, fork.text
    fork_id = fork.json()["id"]
    next_fork = api.post(f"/v1/conversations/{fork_id}/messages", headers=AUTH, json={"prompt": "fork later", "mode": "chat"}).json()["id"]
    fork_history = api.get(f"/internal/runs/{next_fork}/model-context", headers=SERVICE).json()["history"]
    assert fork_history[-1]["steers"] == ["补充内容"]
    assert api.get(f"/v1/conversations/{fork_id}", headers=AUTH).json()["queue"] == []


def test_notification_and_dispatch_rollback_with_terminal_transaction(api, monkeypatch):  # noqa: F811
    from ailab_platform.api import inbox

    conv, run, headers = conversation(api)
    qid = enqueue(api, conv).json()["id"]
    with SessionLocal() as db:
        db.get(Run, run).created_at = utcnow() - timedelta(seconds=61)
        db.commit()
    original = inbox.dispatch_next

    def crash(db, conv):
        original(db, conv)
        raise RuntimeError("before commit")

    monkeypatch.setattr(inbox, "dispatch_next", crash)
    with pytest.raises(RuntimeError, match="before commit"):
        finish(api, run, headers)
    with SessionLocal() as db:
        assert db.get(Run, run).status == "running"
        assert db.get(QueuedMessage, qid).state == "queued"
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.run_id == run)) == 0
    monkeypatch.setattr(inbox, "dispatch_next", original)
    assert finish(api, run, headers).status_code == 200
    with SessionLocal() as db:
        assert db.get(QueuedMessage, qid).state == "started"
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.run_id == run)) == 1


def test_claim_requires_agent_scope_and_attempt_bound_to_run(api, monkeypatch):  # noqa: F811
    from ailab_platform.ports.service_auth import ServicePrincipal
    from ailab_platform.service_auth import authenticator

    conv, run, headers = conversation(api)
    _, other, other_headers = conversation(api)
    enqueue(api, conv, steer=True)
    assert api.post(f"/internal/runs/{run}/steer/claim", headers=other_headers).status_code == 409
    original = authenticator.authenticate
    monkeypatch.setattr(
        authenticator,
        "authenticate",
        lambda token: ServicePrincipal("broker", frozenset({"runs:read"})) if token == "broker" else original(token),
    )
    assert api.post(f"/internal/runs/{run}/steer/claim", headers={"X-Service-Token": "broker"}).status_code == 403
    assert api.post(f"/internal/runs/{other}/steer/claim", headers=headers).status_code == 409


def test_dispatch_admission_failure_pauses_without_losing_terminal(api, monkeypatch):  # noqa: F811
    from fastapi import HTTPException

    from ailab_platform.api import routes_runs

    conv, run, headers = conversation(api)
    qid = enqueue(api, conv).json()["id"]

    def denied(*args, **kwargs):
        raise HTTPException(429, "quota")

    monkeypatch.setattr(routes_runs, "enforce_token_quota", denied)
    assert finish(api, run, headers).status_code == 200
    assert detail(api, conv)["queue_paused"] is True
    with SessionLocal() as db:
        assert db.get(Run, run).status == "succeeded"
        assert db.get(QueuedMessage, qid).state == "queued"


def test_notify_config_bounds():
    from pydantic import ValidationError

    from ailab_platform.config import Settings

    for value in [9, 3601]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, notify_after_seconds=value)
    assert Settings(_env_file=None).notify_after_seconds == 60


def test_inbox_queue_ownership_and_schedule_origin(api):  # noqa: F811
    from tests.test_conversations import _other_user
    from tests.test_schedules_citations import body

    conv, run, _headers = conversation(api)
    qid = enqueue(api, conv).json()["id"]
    other = _other_user(api)
    assert api.post(f"/v1/conversations/{conv}/queue", headers=other, json={"prompt": "foreign"}).status_code == 404
    assert api.patch(f"/v1/conversations/{conv}/queue/{qid}", headers=other, json={"steer": True}).status_code == 404
    with SessionLocal() as db:
        note = create_notification(db, db.get(Run, run).user_id, "share.received", "private")
        note_id = note.id
        db.commit()
    assert api.post("/v1/notifications/read", headers=other, json={"ids": [note_id]}).json() == {"unread": 0}
    with SessionLocal() as db:
        assert db.get(Notification, note_id).read_at is None
    task = api.post("/v1/schedules", headers=other, json=body()).json()
    started = api.post(f"/v1/schedules/{task['id']}/run", headers=other).json()
    sid = started["run_id"]
    with SessionLocal() as db:
        assert db.get(Run, sid).options["schedule_id"] == task["id"]
    h = {**SERVICE, "X-Run-Attempt": claim(api, sid)["token"]}
    api.post(f"/internal/runs/{sid}/status", headers=h, json={"status": "running"})
    finish(api, sid, h)
    notes = api.get("/v1/notifications", headers=other).json()["items"]
    assert notes[0]["kind"] == "schedule.succeeded" and notes[0]["title"] == task["title"]


def test_wait_notification_stays_deduplicated_after_pruning(api):  # noqa: F811
    _conv, run, headers = conversation(api)
    assert save(api, run, headers).status_code == 200
    assert pause(api, run, headers).status_code == 200
    with SessionLocal() as db:
        uid = db.get(Run, run).user_id
        for _ in range(201):
            create_notification(db, uid, "share.received", "prune waiting")
        db.commit()
    assert api.post(f"/v1/runs/{run}/answer", headers=AUTH, json={"question_id": run + ":q", "answer": "A"}).status_code == 202
    job = api.get(f"/internal/runs/{run}/state", headers=SERVICE).json()["job_id"]
    headers = {**SERVICE, "X-Run-Attempt": claim(api, job)["token"]}
    assert api.post(f"/internal/runs/{run}/status", headers=headers, json={"status": "running"}).status_code == 200
    assert pause(api, run, headers).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Notification).where(Notification.run_id == run)) == 0


def test_active_switch_pauses_queue(api):  # noqa: F811
    from ailab_platform.models import Conversation

    conv, run, headers = conversation(api)
    enqueue(api, conv)
    finish(api, run, headers, "failed")
    replacement = api.post(f"/v1/runs/{run}/edit", headers=AUTH, json={"prompt": "alternate"}).json()["id"]
    h = {**SERVICE, "X-Run-Attempt": claim(api, replacement)["token"]}
    api.post(f"/internal/runs/{replacement}/status", headers=h, json={"status": "running"})
    finish(api, replacement, h)
    with SessionLocal() as db:
        db.get(Conversation, conv).queue_paused = False
        db.commit()
    response = api.post(f"/v1/conversations/{conv}/active", headers=AUTH, json={"run_id": run})
    assert response.status_code == 200 and response.json()["queue_paused"] is True
