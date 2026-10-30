"""A lost POST response must not create a second turn, event or dispatch."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import OutboxMessage, Run, RunEvent
from tests.test_conversations import AUTH, _other_user, _status


@pytest.fixture
def api():
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("conversation", [False, True])
def test_retry_returns_current_original_run_and_exactly_one_dispatch(api, conversation):
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json() if conversation else None
    url = f"/v1/conversations/{conv['id']}/messages" if conv else "/v1/runs"
    key = f"lost-response-{conversation}"
    body = {"prompt": "send once", "client_send_id": key}
    first = api.post(url, headers=AUTH, json=body)
    assert first.status_code == 201, first.text
    rid = first.json()["id"]
    # Retrying a busy conversation must succeed, even though an ordinary new send is forbidden.
    repeated = api.post(url, headers=AUTH, json=body)
    assert repeated.status_code == 201 and repeated.json()["id"] == rid
    _status(api, rid, "running")
    _status(api, rid, "succeeded", summary="finished")
    replay = api.post(url, headers=AUTH, json=body)
    assert replay.json()["id"] == rid and replay.json()["status"] == "succeeded"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Run).where(Run.client_send_id == key)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxMessage).where(OutboxMessage.id == rid)) == 1
        assert db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == "run.created")) == 1


def test_simultaneous_identical_retries_serialize_before_admission(api):
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()
    url = f"/v1/conversations/{conv['id']}/messages"
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: api.post(url, headers=AUTH, json={"prompt": "same", "client_send_id": "race"}), range(6)))
    assert [r.status_code for r in responses] == [201] * 6
    assert len({r.json()["id"] for r in responses}) == 1


def test_send_identity_is_user_scoped_and_content_and_conversation_bound(api):
    body = {"prompt": "original", "client_send_id": "global-key"}
    first = api.post("/v1/runs", headers=AUTH, json=body)
    changed = api.post("/v1/runs", headers=AUTH, json={**body, "prompt": "changed"})
    assert changed.status_code == 409
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()
    assert api.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json=body).status_code == 409
    other = api.post("/v1/runs", headers=_other_user(api), json=body)
    assert other.status_code == 201 and other.json()["id"] != first.json()["id"]


@pytest.mark.parametrize("key", ["", "x" * 65, 123])
def test_invalid_send_identity_is_rejected(api, key):
    assert api.post("/v1/runs", headers=AUTH, json={"prompt": "x", "client_send_id": key}).status_code == 422


def test_omitting_id_keeps_distinct_submissions_and_max_length_is_allowed(api):
    one = api.post("/v1/runs", headers=AUTH, json={"prompt": "x"}).json()
    two = api.post("/v1/runs", headers=AUTH, json={"prompt": "x"}).json()
    assert one["id"] != two["id"]
    assert api.post("/v1/runs", headers=AUTH, json={"prompt": "x", "client_send_id": "x" * 64}).status_code == 201


def test_queue_retry_after_dispatch_and_completion_creates_no_second_call(api):
    from ailab_platform.models import QueuedMessage
    from tests.test_inbox_queue import conversation, enqueue, finish
    from tests.test_ledger_protocol import SERVICE
    from tests.test_transactional_jobs import claim

    conv, run, headers = conversation(api)
    key = "queue-dispatched"
    first = enqueue(api, conv, client_send_id=key)
    assert first.status_code == 201, first.text
    qid = first.json()["id"]
    assert finish(api, run, headers).status_code == 200
    with SessionLocal() as db:
        dispatched = db.get(QueuedMessage, qid).run_id
        assert dispatched and db.get(QueuedMessage, qid).client_send_id == key
    h = {**SERVICE, "X-Run-Attempt": claim(api, dispatched)["token"]}
    assert api.post(f"/internal/runs/{dispatched}/status", headers=h, json={"status": "running"}).status_code == 200
    assert finish(api, dispatched, h).status_code == 200
    for _ in range(2):
        retry = enqueue(api, conv, client_send_id=key)
        assert retry.status_code == 201 and retry.json()["id"] == qid and retry.json()["state"] == "started"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(QueuedMessage).where(QueuedMessage.conversation_id == conv)) == 1
        assert db.scalar(select(func.count()).select_from(Run).where(Run.conversation_id == conv)) == 2
        assert db.scalar(select(func.count()).select_from(OutboxMessage).where(OutboxMessage.id == dispatched)) == 1


def test_queue_applied_steer_replay_and_fork_identity(api):
    from ailab_platform.adapters.runtime import fresh_state
    from ailab_platform.models import QueuedMessage
    from tests.test_inbox_queue import conversation, enqueue, finish
    from tests.test_runtime_protocol import save

    conv, run, headers = conversation(api)
    body = {"client_send_id": "queue-steered", "steer": True}
    qid = enqueue(api, conv, **body).json()["id"]
    assert api.post(f"/internal/runs/{run}/steer/claim", headers=headers).status_code == 200
    state = fresh_state()
    state.update(messages=[{"role": "user", "content": "next"}], steer_ids=[qid])
    assert save(api, run, headers, state=state).status_code == 200
    assert finish(api, run, headers).status_code == 200
    retry = enqueue(api, conv, **body)
    assert retry.status_code == 201 and retry.json()["id"] == qid and retry.json()["state"] == "steered"
    fork = api.post(f"/v1/conversations/{conv}/fork", headers=AUTH, json={"run_id": run, "mode": "chat"})
    assert fork.status_code == 201, fork.text
    with SessionLocal() as db:
        copied = db.scalars(select(QueuedMessage).where(QueuedMessage.conversation_id == fork.json()["id"])).one()
        assert copied.client_send_id is None
        assert db.get(QueuedMessage, qid).client_send_id == body["client_send_id"]
        assert db.scalar(select(func.count()).select_from(Run).where(Run.conversation_id == conv)) == 1
        assert db.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.run_id == run, RunEvent.type == "agent.steered")) == 1


@pytest.mark.parametrize(
    "changed",
    [{"prompt": "different"}, {"steer": True}, {"file_ids": ["missing"]}, {"mode": "work"}, {"model": "test-other"}, {"skill": "x"}],
)
def test_queue_identity_rejects_changed_payload_before_admission(api, changed):
    from tests.test_inbox_queue import conversation, enqueue

    conv, _, _ = conversation(api)
    key = "queue-change-" + next(iter(changed))
    assert enqueue(api, conv, client_send_id=key).status_code == 201
    assert enqueue(api, conv, client_send_id=key, **changed).status_code == 409
    other = api.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
    assert enqueue(api, other, client_send_id=key).status_code == 409


def test_queue_replay_at_capacity_and_after_cancel_does_not_resurrect(api):
    from ailab_platform.models import QueuedMessage
    from tests.test_inbox_queue import conversation, enqueue

    conv, _, _ = conversation(api)
    key = "queue-cancel"
    qid = enqueue(api, conv, client_send_id=key).json()["id"]
    for _ in range(4):
        assert enqueue(api, conv).status_code == 201
    retry = enqueue(api, conv, client_send_id=key)
    assert retry.status_code == 201 and retry.json()["id"] == qid
    assert api.delete(f"/v1/conversations/{conv}/queue/{qid}", headers=AUTH).status_code == 204
    retry = enqueue(api, conv, client_send_id=key)
    assert retry.status_code == 201 and retry.json()["state"] == "canceled"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(QueuedMessage).where(QueuedMessage.conversation_id == conv)) == 5


def test_queue_concurrent_identical_posts(api):
    from ailab_platform.models import QueuedMessage
    from tests.test_inbox_queue import conversation, enqueue

    conv, _, _ = conversation(api)
    with ThreadPoolExecutor(max_workers=6) as pool:
        responses = list(pool.map(lambda _: enqueue(api, conv, client_send_id="queue-race"), range(6)))
    assert [r.status_code for r in responses] == [201] * 6
    assert len({r.json()["id"] for r in responses}) == 1
    with SessionLocal() as db:
        assert db.get(QueuedMessage, responses[0].json()["id"]).client_send_id == "queue-race"
        assert db.scalar(select(func.count()).select_from(QueuedMessage).where(QueuedMessage.conversation_id == conv)) == 1


def test_queue_unique_constraint_race_returns_winner(api, monkeypatch):
    from sqlalchemy.orm import Session

    from ailab_platform.models import QueuedMessage
    from tests.test_inbox_queue import conversation, enqueue

    conv, _, _ = conversation(api)
    winner = enqueue(api, conv, client_send_id="queue-db-race").json()["id"]
    scalar = Session.scalar
    missed = False

    def miss_first_lookup(self, statement, *args, **kwargs):
        nonlocal missed
        if not missed and statement.column_descriptions[0].get("entity") is QueuedMessage:
            missed = True  # Simulate the winner committing after our initial lookup.
            return None
        return scalar(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "scalar", miss_first_lookup)
    response = enqueue(api, conv, client_send_id="queue-db-race")
    assert missed and response.status_code == 201 and response.json()["id"] == winner


def test_direct_and_queue_identities_cannot_cross_endpoints(api):
    from tests.test_inbox_queue import conversation, enqueue

    conv, _, _ = conversation(api)
    key = "direct-to-queue"
    assert api.post("/v1/runs", headers=AUTH, json={"prompt": "next", "client_send_id": key}).status_code == 201
    assert enqueue(api, conv, client_send_id=key).status_code == 409
    key = "queue-to-direct"
    assert enqueue(api, conv, client_send_id=key).status_code == 201
    assert api.post("/v1/runs", headers=AUTH, json={"prompt": "next", "client_send_id": key}).status_code == 409


def test_identified_queue_rows_can_be_reaped_without_resurrection(api, monkeypatch):
    from datetime import timedelta

    from ailab_platform.api.reaper import purge_deleted_conversations
    from ailab_platform.config import settings
    from ailab_platform.models import Conversation, QueuedMessage, utcnow
    from tests.test_inbox_queue import conversation, enqueue, finish

    conv, run, headers = conversation(api)
    qid = enqueue(api, conv, client_send_id="queue-reaped").json()["id"]
    with SessionLocal() as db:
        assert db.get(QueuedMessage, qid).client_send_id == "queue-reaped"
    assert finish(api, run, headers, "canceled").status_code == 200
    assert api.delete(f"/v1/conversations/{conv}", headers=AUTH).status_code == 204
    monkeypatch.setattr(settings, "conversation_purge_days", 1)
    with SessionLocal() as db:
        db.get(Conversation, conv).deleted_at = utcnow() - timedelta(days=2)
        db.commit()
        for _ in range(10):
            purge_deleted_conversations(db, utcnow())
            if db.get(Conversation, conv, populate_existing=True) is None:
                break
        assert db.get(Conversation, conv) is None
        assert db.get(QueuedMessage, qid) is None
    assert enqueue(api, conv, client_send_id="queue-reaped").status_code == 404
