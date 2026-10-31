"""Runtime fence, checkpoint CAS, delivery identity and continuation transactions."""

import gzip
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from ailab_platform.adapters.runtime import fresh_state
from ailab_platform.api.reaper import expire_runtime_questions, lifecycle_once
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import OutboxMessage, Run, RunCheckpoint
from ailab_platform.storage import get_storage
from tests.test_ledger_protocol import AUTH, SERVICE, api  # noqa: F401
from tests.test_transactional_jobs import action, claim, start


def owned(api):  # noqa: F811
    rid = start(api)
    token = claim(api, rid)["token"]
    headers = {**SERVICE, "X-Run-Attempt": token}
    response = api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"})
    assert response.status_code == 200, response.text
    return rid, token, headers


def save(api, rid, headers, step=0, state=None):  # noqa: F811
    return api.put(f"/internal/runs/{rid}/checkpoint", headers=headers, json={"expected_step": step, "state": state or fresh_state()})


def pause(api, rid, headers):  # noqa: F811
    return api.post(
        f"/internal/runs/{rid}/pause",
        headers=headers,
        json={
            "status": "waiting_user",
            "question": {"question_id": rid + ":q", "question": "选哪个？", "options": ["A", "B"], "allow_text": False},
        },
    )


def test_all_legacy_mutations_require_live_claim(api):  # noqa: F811
    rid, token, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    for suffix, body in [
        ("status", {"status": "succeeded"}),
        ("transition", {"status": "succeeded"}),
        ("events", {"type": "forged", "payload": {}}),
    ]:
        assert api.post(f"/internal/runs/{rid}/{suffix}", headers=SERVICE, json=body).status_code == 409
    assert action(api, rid, "release", token).status_code == 200
    next_token = claim(api, rid)["token"]
    assert next_token != token
    for suffix, body in [
        ("status", {"status": "succeeded"}),
        ("transition", {"status": "succeeded", "attempt_id": token}),
        ("events", {"type": "old"}),
    ]:
        assert api.post(f"/internal/runs/{rid}/{suffix}", headers=headers, json=body).status_code == 409
    assert save(api, rid, headers, 1).status_code == 409
    summary = {"through_run_id": rid, "summary": "old"}
    assert api.put(f"/internal/runs/{rid}/model-summary", headers=headers, json=summary).status_code == 409
    reservation = {
        "call_id": uuid4().hex,
        "run_id": rid,
        "attempt_id": token,
        "alias": "test-fast",
        "mode": "work",
        "max_output_tokens": 10,
        "estimated_input_tokens": 1,
    }
    assert api.post("/internal/usage/reserve", headers=headers, json=reservation).status_code == 409
    artifact = {"run_id": rid, "staging_key": "staging/missing", "filename": "f.txt", "size": 1, "sha256": "x"}
    assert api.post("/internal/artifacts", headers={**headers, "X-Delivery-Id": rid + ":tool"}, json=artifact).status_code == 409


def test_checkpoint_cas_gzip_recent_two_and_seven_day_cleanup(api):  # noqa: F811
    rid, _, headers = owned(api)
    for step in range(3):
        assert save(api, rid, headers, step).json() == {"step": step + 1}
    assert save(api, rid, headers, 1).status_code == 409
    result = api.get(f"/internal/runs/{rid}/checkpoint", headers=headers).json()
    assert result["state"]["runtime_version"] == 1 and result["step"] == 3
    with SessionLocal() as db:
        rows = db.scalars(select(RunCheckpoint).where(RunCheckpoint.run_id == rid)).all()
        assert sorted(r.step for r in rows) == [2, 3]
        assert json.loads(gzip.decompress(get_storage().get_bytes(rows[0].state_key)))["runtime_version"] == 1
    assert api.post(f"/internal/runs/{rid}/status", headers=headers, json={"status": "succeeded"}).status_code == 200
    with SessionLocal() as db:
        rows = db.scalars(select(RunCheckpoint).where(RunCheckpoint.run_id == rid)).all()
        assert all(row.expires_at is not None for row in rows)
        lifecycle_once(db, now=datetime.now(UTC) + timedelta(days=8))
        assert db.scalars(select(RunCheckpoint).where(RunCheckpoint.run_id == rid)).all() == []


def test_finished_conversation_checkpoint_is_available_to_child_and_survives_expiry(api):  # noqa: F811
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()
    parent = api.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "first"}).json()
    from tests.run_claims import owned_headers

    headers = owned_headers(api, parent["id"])
    assert api.post(f"/internal/runs/{parent['id']}/transition", headers=headers, json={"status": "running"}).status_code == 200
    state = fresh_state()
    state.update(
        finished=True,
        messages=[{"role": "user", "content": "first"}, {"role": "assistant", "content": "answer"}],
        tool_catalog_v1=[],
        context_model=parent["model"],
    )
    assert save(api, parent["id"], headers, state=state).status_code == 200
    assert api.post(f"/internal/runs/{parent['id']}/status", headers=headers, json={"status": "succeeded"}).status_code == 200
    with SessionLocal() as db:
        lifecycle_once(db, now=datetime.now(UTC) + timedelta(days=8))
        assert db.get(RunCheckpoint, (parent["id"], 1)).expires_at is None
    child = api.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "second"}).json()
    result = api.get(f"/internal/runs/{child['id']}/model-input", headers=SERVICE).json()
    assert result["previous_model_context"] == {"messages": state["messages"], "tools": []}


def test_wait_answer_new_job_old_ack_cannot_delete_continuation(api):  # noqa: F811
    rid, token, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    assert pause(api, rid, headers).json()["status"] == "waiting_user"
    assert api.post(f"/v1/runs/{rid}/answer", headers=AUTH, json={"question_id": rid + ":q", "answer": "C"}).status_code == 422
    body = {"question_id": rid + ":q", "answer": "A"}
    answered = api.post(f"/v1/runs/{rid}/answer", headers=AUTH, json=body)
    assert answered.status_code == 202, answered.text
    assert api.post(f"/v1/runs/{rid}/answer", headers=AUTH, json=body).status_code == 409
    with SessionLocal() as db:
        run = db.get(Run, rid)
        next_job = run.runtime_meta["job_id"]
        assert next_job != rid
        assert db.get(OutboxMessage, next_job) is not None
    assert action(api, rid, "ack", token).status_code == 200
    assert action(api, rid, "ack", token).status_code == 200
    with SessionLocal() as db:
        assert db.get(OutboxMessage, next_job) is not None
    assert claim(api, next_job)["state"] == "claimed"
    assert api.post(f"/internal/runs/{rid}/events", headers=headers, json={"type": "old"}).status_code == 409


def test_wait_timeout_still_accepts_answer(api):  # noqa: F811
    rid, _, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    assert pause(api, rid, headers).status_code == 200
    with SessionLocal() as db:
        run = db.get(Run, rid)
        run.waiting_since = datetime.now(UTC) - timedelta(hours=settings.ask_user_timeout_hours + 1)
        db.commit()
        assert expire_runtime_questions(db, datetime.now(UTC)) >= 1
    assert api.post(f"/v1/runs/{rid}/answer", headers=AUTH, json={"question_id": rid + ":q", "answer": "A"}).status_code == 202


def test_expired_attempt_can_settle_only_its_frozen_call(api):  # noqa: F811
    rid, token, headers = owned(api)
    call = {
        "call_id": uuid4().hex,
        "run_id": rid,
        "attempt_id": token,
        "alias": "test-fast",
        "mode": "work",
        "max_output_tokens": 10,
        "estimated_input_tokens": 1,
    }
    assert api.post("/internal/usage/reserve", headers=headers, json=call).status_code == 200
    assert action(api, rid, "release", token).status_code == 200
    new = claim(api, rid)["token"]
    usage = {
        "call_id": call["call_id"],
        "attempt_id": token,
        "actual_backend": "test-fast",
        "input_tokens": 2,
        "output_tokens": 3,
        "status": "settled",
    }
    assert api.post("/internal/usage/settle", headers=SERVICE, json={**usage, "attempt_id": new}).status_code == 409
    assert api.post("/internal/usage/settle", headers=SERVICE, json=usage).json()["run_tokens"] == 5
    assert api.post("/internal/usage/settle", headers=SERVICE, json=usage).json()["run_tokens"] == 5


def test_a_job_that_keeps_dying_fails_the_run_instead_of_looping(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "run_max_attempts", 2)
    rid = start(api)
    for _ in range(2):  # each claim expires unacked, as a killed Agent leaves it
        claim(api, rid)
        with SessionLocal() as db:
            db.get(OutboxMessage, rid).claimed_until = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()
    assert claim(api, rid) == {"state": "done"}
    with SessionLocal() as db:
        run = db.get(Run, rid)
        assert run.status == "failed" and "giving up" in run.error
        assert db.get(OutboxMessage, rid) is None


def test_one_automatic_continuation_then_the_user_decides(api):  # noqa: F811
    rid, _, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    lost = {"status": "interrupted", "error": "lease lost", "auto_resume": True}
    assert api.post(f"/internal/runs/{rid}/pause", headers=headers, json=lost).json()["status"] == "queued"
    with SessionLocal() as db:
        first_job = db.get(Run, rid).runtime_meta["job_id"]
    second = claim(api, first_job)
    assert second["state"] == "claimed" and second["attempt"] == 1
    again = {**SERVICE, "X-Run-Attempt": second["attempt_id"]}
    assert api.post(f"/internal/runs/{rid}/transition", headers=again, json={"status": "running"}).status_code == 200
    assert api.post(f"/internal/runs/{rid}/pause", headers=again, json=lost).json()["status"] == "interrupted"
    assert api.post(f"/v1/runs/{rid}/resume", headers=AUTH).status_code == 200  # the user continues it: budget renewed


def _claimed_again(api, rid):  # noqa: F811
    with SessionLocal() as db:
        job = db.get(Run, rid).runtime_meta["job_id"]
    attempt = claim(api, job)
    headers = {**SERVICE, "X-Run-Attempt": attempt["attempt_id"]}
    assert api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"}).status_code == 200
    return headers


def test_reclaims_resume_on_their_own_budget_of_three(api):  # noqa: F811
    rid, _, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    reclaimed = {"status": "interrupted", "error": "reclaimed", "auto_resume": True, "evicted": True}
    for _ in range(3):
        assert api.post(f"/internal/runs/{rid}/pause", headers=headers, json=reclaimed).json()["status"] == "queued"
        headers = _claimed_again(api, rid)
    # then the user decides
    assert api.post(f"/internal/runs/{rid}/pause", headers=headers, json=reclaimed).json()["status"] == "interrupted"
    with SessionLocal() as db:  # the file shares one database: leave no waiting run behind for the expiry tests
        db.get(Run, rid).status = "canceled"
        db.commit()


def test_a_reclaim_does_not_use_up_the_automatic_resume_for_faults(api):  # noqa: F811
    rid, _, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    reclaimed = {"status": "interrupted", "error": "reclaimed", "auto_resume": True, "evicted": True}
    fault = {"status": "interrupted", "error": "lease lost", "auto_resume": True}
    assert api.post(f"/internal/runs/{rid}/pause", headers=headers, json=reclaimed).json()["status"] == "queued"
    headers = _claimed_again(api, rid)
    assert api.post(f"/internal/runs/{rid}/pause", headers=headers, json=fault).json()["status"] == "queued"


def test_an_interrupted_run_nobody_continues_is_canceled_in_time(api):  # noqa: F811
    from ailab_platform.api.reaper import expire_interrupted_runs

    rid, _, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    assert api.post(f"/internal/runs/{rid}/pause", headers=headers, json={"status": "interrupted", "error": "lease"}).status_code == 200
    with SessionLocal() as db:
        run = db.get(Run, rid)
        run.waiting_since = datetime.now(UTC) - timedelta(hours=settings.ask_user_timeout_hours + 1)
        db.commit()
        assert expire_interrupted_runs(db, datetime.now(UTC)) == 1
        assert db.get(Run, rid).status == "canceled"


def test_a_pending_question_never_cancels_its_interrupted_run(api):  # noqa: F811
    from ailab_platform.api.reaper import expire_interrupted_runs, expire_runtime_questions

    rid, _, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    assert pause(api, rid, headers).status_code == 200
    with SessionLocal() as db:
        run = db.get(Run, rid)
        run.waiting_since = datetime.now(UTC) - timedelta(hours=settings.ask_user_timeout_hours + 1)
        db.commit()
        expire_runtime_questions(db, datetime.now(UTC))
        assert db.get(Run, rid).status == "interrupted"
        assert expire_interrupted_runs(db, datetime.now(UTC) + timedelta(days=30)) == 0
    assert api.post(f"/v1/runs/{rid}/answer", headers=AUTH, json={"question_id": rid + ":q", "answer": "A"}).status_code == 202


@pytest.mark.parametrize("answer", [True, False])
def test_continuation_rechecks_user_capacity_without_consuming_answer(api, monkeypatch, answer):  # noqa: F811
    from ailab_platform import quota
    from ailab_platform.domain.quota_policy import Limits

    rid, _, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    body = (
        {"status": "waiting_user", "question": {"question_id": rid + ":q", "question": "Which?"}} if answer else {"status": "interrupted"}
    )
    assert api.post(f"/internal/runs/{rid}/pause", headers=headers, json=body).status_code == 200
    start(api)  # another run uses the slot released by the paused run
    monkeypatch.setattr(quota, "user_limits", lambda *a: Limits(1_000_000_000, 1_000_000_000, 100_000_000, 1))
    suffix = "answer" if answer else "resume"
    response = api.post(f"/v1/runs/{rid}/{suffix}", headers=AUTH, json={"question_id": rid + ":q", "answer": "A"} if answer else {})
    assert response.status_code == 429
    with SessionLocal() as db:
        run = db.get(Run, rid)
        assert run.status == body["status"]
        assert not (run.runtime_meta or {}).get("answer")


def test_deleting_conversation_cancels_waiting_run(api):  # noqa: F811
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
    rid = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "ask"}).json()["id"]
    token = claim(api, rid)["token"]
    headers = {**SERVICE, "X-Run-Attempt": token}
    assert api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"}).status_code == 200
    assert save(api, rid, headers).status_code == 200
    assert pause(api, rid, headers).status_code == 200
    assert api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "another"}).status_code == 409
    assert api.delete(f"/v1/conversations/{conv}", headers=AUTH).status_code == 204
    assert api.get(f"/v1/runs/{rid}", headers=AUTH).json()["status"] == "canceled"
    assert api.post(f"/v1/runs/{rid}/answer", headers=AUTH, json={"question_id": rid + ":q", "answer": "A"}).status_code == 409


def test_pausing_revokes_business_writes_before_transport_ack(api):  # noqa: F811
    rid, _, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    assert pause(api, rid, headers).status_code == 200
    assert api.post(f"/internal/runs/{rid}/events", headers=headers, json={"type": "late"}).status_code == 409
    assert api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "canceled"}).status_code == 409


def test_paused_event_stream_delivers_the_question_and_closes(api):  # noqa: F811
    rid, _, headers = owned(api)
    assert save(api, rid, headers).status_code == 200
    assert pause(api, rid, headers).status_code == 200
    stream = api.get(f"/v1/runs/{rid}/events", headers=AUTH)
    assert "event: agent.ask_user" in stream.text
    assert stream.text.endswith("event: stream.end\ndata: {}\n\n")


@pytest.mark.parametrize("reader", ["next_turn", "fork"])
def test_resumed_crash_restores_latest_workspace_after_superseded_deletion(api, reader):  # noqa: F811
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
    response = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "work", "mode": "work"})
    assert response.status_code == 201, response.text
    rid = response.json()["id"]
    headers = {**SERVICE, "X-Run-Attempt": claim(api, rid)["token"]}
    assert api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"}).status_code == 200
    storage = get_storage()
    old, new = f"workspaces/{rid}/old.tgz", f"workspaces/{rid}/new.tgz"
    storage.put_bytes(old, b"old archive")
    storage.put_bytes(new, b"latest archive")
    state = fresh_state()
    state["workspace"] = {"key": old, "saved_at": (datetime.now(UTC) - timedelta(days=10)).isoformat(), "bytes": 11}
    assert save(api, rid, headers, state=state).status_code == 200
    response = api.post(
        f"/internal/runs/{rid}/pause",
        headers=headers,
        json={
            "status": "interrupted",
            "result": {
                "workspace_saved": True,
                "workspace_key": old,
                "workspace_saved_at": state["workspace"]["saved_at"],
                "workspace_bytes": 11,
            },
        },
    )
    assert response.status_code == 200, response.text
    assert api.post(f"/v1/runs/{rid}/resume", headers=AUTH).status_code == 200
    with SessionLocal() as db:
        job_id = db.get(Run, rid).runtime_meta["job_id"]
    headers = {**SERVICE, "X-Run-Attempt": claim(api, job_id)["token"]}
    assert api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"}).status_code == 200
    state["workspace"] = {"key": new, "saved_at": datetime.now(UTC).isoformat(), "bytes": 14}
    assert save(api, rid, headers, step=1, state=state).status_code == 200
    assert not storage.exists(old)
    response = api.post(
        f"/internal/runs/{rid}/transition", headers=headers, json={"status": "failed", "error": "crash", "result": {"sources": []}}
    )
    assert response.status_code == 200, response.text
    if reader == "fork":
        response = api.post(f"/v1/conversations/{conv}/fork", headers=AUTH, json={"run_id": rid, "mode": "work"})
        assert response.status_code == 201, response.text
        conv = response.json()["id"]
        storage.delete(new)  # the fork must own an independent copy
    response = api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "continue", "mode": "work"})
    assert response.status_code == 201, response.text
    next_id = response.json()["id"]
    for route in (f"/internal/runs/{next_id}", f"/internal/runs/{next_id}/model-input"):
        restored = api.get(route, headers=SERVICE).json()
        assert restored["workspace_expired"] is False
        assert restored["workspace_restore_key"]
        assert storage.get_bytes(restored["workspace_restore_key"]) == b"latest archive"


def test_superseded_workspace_deletion_waits_for_winning_checkpoint(api):  # noqa: F811
    from ailab_platform.api.reaper import drain_object_deletions
    from ailab_platform.models import LifecycleDeletion

    rid, _, headers = owned(api)
    storage = get_storage()
    old, new = f"workspaces/{rid}/old.tgz", f"workspaces/{rid}/new.tgz"
    for key in (old, new):
        storage.put_bytes(key, b"archive", "application/gzip")
    state = fresh_state()
    state["workspace"] = {"key": old}
    assert save(api, rid, headers, state=state).status_code == 200
    state["workspace"] = {"key": new}
    assert save(api, rid, headers, step=0, state=state).status_code == 409
    with SessionLocal() as db:
        assert not db.scalar(select(LifecycleDeletion.id).where(LifecycleDeletion.key == old))
    assert save(api, rid, headers, step=1, state=state).status_code == 200
    assert not storage.exists(old) and storage.exists(new)
    with SessionLocal() as db:
        assert db.scalar(select(LifecycleDeletion.id).where(LifecycleDeletion.key == old))
        drain_object_deletions(db, storage)
    assert not storage.exists(old) and storage.exists(new)
    assert api.get(f"/internal/runs/{rid}/checkpoint", headers=headers).json()["state"]["workspace"]["key"] == new


@pytest.mark.parametrize("kind", ["answer", "automatic", "budget"])
def test_only_explicit_budget_resume_resets_window(api, kind):  # noqa: F811
    rid, _, headers = owned(api)
    state = fresh_state()
    state["budget"] = {"window_id": rid, "active_seconds": 99, "start_step": 0}
    assert save(api, rid, headers, state=state).status_code == 200
    if kind == "answer":
        assert pause(api, rid, headers).status_code == 200
        response = api.post(f"/v1/runs/{rid}/answer", headers=AUTH, json={"question_id": rid + ":q", "answer": "A"})
    else:
        response = api.post(
            f"/internal/runs/{rid}/pause",
            headers=headers,
            json={
                "status": "interrupted",
                "auto_resume": kind == "automatic",
                "result": {"pause": {"kind": "budget"}} if kind == "budget" else {},
            },
        )
        assert response.status_code == 200
        if kind == "budget":
            response = api.post(f"/v1/runs/{rid}/resume", headers=AUTH)
    assert response.status_code in {200, 202}, response.text
    body = api.get(f"/internal/runs/{rid}", headers=SERVICE).json()
    assert (body["budget_window_id"] != rid) == (kind == "budget")
