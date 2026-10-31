"""API business-CAS ownership, scoped ACK and transaction boundaries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from ailab_platform.api import jobs
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import OutboxMessage, Run, RunAttempt
from tests.test_ledger_protocol import AUTH, SERVICE, api  # noqa: F401


def start(api, mode="work"):  # noqa: F811
    r = api.post("/v1/runs", headers=AUTH, json={"prompt": "job", "model": "test-fast", "mode": mode})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def claim(api, job_id):  # noqa: F811
    response = api.post(f"/internal/jobs/{job_id}/claim", headers=SERVICE, json={"consumer": "test"})
    assert response.status_code == 200, response.text
    return response.json()


def action(api, job_id, action, token):  # noqa: F811
    return api.post(f"/internal/jobs/{job_id}/{action}", headers=SERVICE, json={"token": token})


def test_duplicate_claims_cas_once_and_ack_is_idempotent(api):  # noqa: F811
    rid = start(api)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: claim(api, rid), range(4)))
    owned = [r for r in results if r["state"] == "claimed"]
    assert len(owned) == 1
    assert owned[0]["attempt"] == 1 and owned[0]["attempt_id"] == owned[0]["token"]
    assert action(api, rid, "ack", "wrong").status_code == 409
    assert action(api, rid, "ack", owned[0]["token"]).status_code == 200
    assert action(api, rid, "ack", owned[0]["token"]).status_code == 200
    assert claim(api, rid) == {"state": "done"}


def test_release_and_crash_lease_expiry_preserve_attempt_numbers(api):  # noqa: F811
    rid = start(api)
    first = claim(api, rid)
    assert action(api, rid, "release", first["token"]).status_code == 200
    second = claim(api, rid)
    assert second["attempt"] == 2
    assert action(api, rid, "ack", first["token"]).status_code == 409
    with SessionLocal() as db:
        job = db.get(OutboxMessage, rid)
        job.claimed_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    third = claim(api, rid)
    assert third["attempt"] == 3
    assert action(api, rid, "release", second["token"]).status_code == 409
    with SessionLocal() as db:
        attempts = db.scalars(select(RunAttempt).where(RunAttempt.run_id == rid).order_by(RunAttempt.number)).all()
        assert [a.outcome for a in attempts] == ["released", "abandoned", None]


def test_consumer_scope_cannot_claim_another_pool(api, monkeypatch):  # noqa: F811
    from ailab_platform.ports.service_auth import ServicePrincipal
    from ailab_platform.service_auth import authenticator

    original = authenticator.authenticate
    monkeypatch.setattr(
        authenticator,
        "authenticate",
        lambda token: ServicePrincipal("worker", frozenset({"jobs:file"})) if token == "worker" else original(token),
    )
    rid = start(api)
    r = api.post(f"/internal/jobs/{rid}/claim", headers={"X-Service-Token": "worker"}, json={"consumer": "bad"})
    assert r.status_code == 403


def test_transition_events_atomic_and_attempt_fence(api, monkeypatch):  # noqa: F811
    from ailab_platform.api import routes_internal

    rid = start(api)
    first = claim(api, rid)
    body = {"status": "running", "events": [{"type": "agent.started", "payload": {"value": 1}}], "attempt_id": first["token"]}
    assert api.post(f"/internal/runs/{rid}/transition", headers=SERVICE, json={**body, "attempt_id": "old"}).status_code == 409
    original = routes_internal.append_event

    def fail(db, run_id, type_, *args, **kwargs):
        if type_ == "agent.started":
            raise RuntimeError("event insert failure")
        return original(db, run_id, type_, *args, **kwargs)

    monkeypatch.setattr(routes_internal, "append_event", fail)
    with pytest.raises(RuntimeError, match="event insert failure"):
        api.post(f"/internal/runs/{rid}/transition", headers=SERVICE, json=body)
    with SessionLocal() as db:
        assert db.get(Run, rid).status == "queued"
    monkeypatch.setattr(routes_internal, "append_event", original)
    assert api.post(f"/internal/runs/{rid}/transition", headers=SERVICE, json=body).status_code == 200
    history = api.get(f"/v1/runs/{rid}/events/history", headers=AUTH).json()
    assert [e["type"] for e in history] == ["run.created", "run.status", "agent.started"]


def test_business_creation_and_outbox_roll_back_together(api, monkeypatch):  # noqa: F811
    prompt = uuid4().hex
    monkeypatch.setattr(jobs, "stage_job", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("outbox unavailable")))
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        api.post("/v1/runs", headers=AUTH, json={"prompt": prompt})
    with SessionLocal() as db:
        assert db.scalar(select(Run).where(Run.prompt == prompt)) is None


def test_terminal_business_state_skips_redelivery(api):  # noqa: F811
    rid = start(api)
    assert api.post(f"/v1/runs/{rid}/cancel", headers=AUTH).status_code == 200
    assert claim(api, rid) == {"state": "done"}
    with SessionLocal() as db:
        assert db.get(OutboxMessage, rid) is None


def test_streams_and_envelope_are_versioned(api):  # noqa: F811
    for mode in ("chat", "work"):
        rid = start(api, mode)
        with SessionLocal() as db:
            job = db.get(OutboxMessage, rid)
            assert job.topic == f"{settings.run_queue}:{mode}"
            assert job.payload["job_id"] == rid
            assert job.payload["kind"] == "run" and job.payload["schema_version"] == 1


def test_worker_duplicate_delivery_executes_once_then_ack(api, monkeypatch):  # noqa: F811
    from ailab_platform.queue import InMemoryQueue
    from ailab_platform.worker import main

    response = api.post("/v1/files", headers=AUTH, files={"upload": ("job.txt", b"payload", "text/plain")})
    assert response.status_code == 201
    fid = response.json()["id"]
    with SessionLocal() as db:
        job = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == fid))
        payload, jid = job.payload, job.id

    class Client:
        def __init__(self, **kwargs):
            pass

        def job_action(self, job_id, action, **body):
            r = api.post(f"/internal/jobs/{job_id}/{action}", headers=SERVICE, json=body)
            assert r.status_code == 200, r.text
            return r.json()

        def close(self):
            pass

    calls = []
    monkeypatch.setattr(main, "PlatformClient", Client)
    monkeypatch.setattr(main, "process_file", lambda file_id: calls.append(file_id) or {"file_id": file_id})
    q = InMemoryQueue()
    q.enqueue(settings.file_queue, payload)
    main.handle_message(q, q.dequeue(settings.file_queue, timeout=0))
    q.enqueue(settings.file_queue, payload)
    main.handle_message(q, q.dequeue(settings.file_queue, timeout=0))
    assert calls == [fid]
    assert q.length(settings.file_queue) == 0
    with SessionLocal() as db:
        assert db.get(OutboxMessage, jid) is None


def test_busy_preview_is_released_for_redelivery_without_acknowledging_job(api, monkeypatch):  # noqa: F811
    from ailab_platform.models import FileObject
    from ailab_platform.queue import InMemoryQueue, job_topic
    from ailab_platform.worker import main

    fid = api.post("/v1/files", headers=AUTH, files={"upload": ("job.pdf", b"payload", "application/pdf")}).json()["id"]
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.status = "ready"
        job = jobs.stage_job(db, "preview", file_id=fid)
        file.meta = {**(file.meta or {}), "preview_job_id": job.id, "preview": {"status": "pending"}}
        payload, jid = job.payload, job.id
        db.commit()

    class Client:
        def __init__(self, **kwargs):
            pass

        def job_action(self, job_id, action, **body):
            result = api.post(f"/internal/jobs/{job_id}/{action}", headers=SERVICE, json=body)
            assert result.status_code == 200
            return result.json()

        def close(self):
            pass

    monkeypatch.setattr(main, "PlatformClient", Client)
    monkeypatch.setattr(main, "process_preview", lambda *args: {"skipped": "busy"})
    topic, queue = job_topic("preview"), InMemoryQueue()
    queue.enqueue(topic, payload)
    main.handle_message(queue, queue.dequeue(topic, timeout=0), topic)
    assert queue.length(topic) == 0
    with SessionLocal() as db:
        job = db.get(OutboxMessage, jid)
        assert job and job.claim_token is None
        assert (db.get(FileObject, fid).meta or {})["preview"]["status"] == "pending"


def test_agent_api_failure_after_runtime_does_not_ack_durable_job(api, monkeypatch):  # noqa: F811
    import asyncio

    from ailab_platform.agent import main
    from ailab_platform.queue import InMemoryQueue

    rid = start(api)
    with SessionLocal() as db:
        payload = db.get(OutboxMessage, rid).payload

    class Client:
        async def job_action(self, job_id, action, **body):
            r = api.post(f"/internal/jobs/{job_id}/{action}", headers=SERVICE, json=body)
            assert r.status_code == 200
            return r.json()

        async def get_run_state(self, *args):
            raise ConnectionError("api down")

        async def aclose(self):
            pass

    class Runtime:
        async def process(self, *args, **kwargs):
            pass

    monkeypatch.setattr(main, "AsyncPlatformClient", Client)
    q = InMemoryQueue()
    q.enqueue(settings.run_queue, payload)
    with pytest.raises(ConnectionError, match="api down"):
        asyncio.run(main.handle_message(Runtime(), q, q.dequeue(settings.run_queue, timeout=0)))
    assert q.length(settings.run_queue) == 1
    with SessionLocal() as db:
        assert db.get(OutboxMessage, rid) is not None


def test_file_expiry_refreshes_meta_under_user_lock(api, monkeypatch):  # noqa: F811
    from contextlib import contextmanager

    response = api.post("/v1/files", headers=AUTH, files={"upload": ("race.txt", b"payload", "text/plain")})
    fid = response.json()["id"]
    with SessionLocal() as db:
        job = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == fid))
        job.payload = {**job.payload, "deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        jid = job.id
        db.commit()
    original = jobs.user_critical_section

    @contextmanager
    def concurrent_patch(db, user_id):
        patch = api.patch(
            f"/internal/files/{fid}", headers=SERVICE, json={"meta": {"thumb_key": "derived/race/thumb.webp", "other_writer": "preserved"}}
        )
        assert patch.status_code == 200
        with original(db, user_id):
            yield

    monkeypatch.setattr(jobs, "user_critical_section", concurrent_patch)
    assert claim(api, jid) == {"state": "done"}
    result = api.get(f"/v1/files/{fid}", headers=AUTH).json()
    assert result["meta"]["other_writer"] == "preserved"
    assert result["meta"]["thumb_key"] == "derived/race/thumb.webp"
    assert result["meta"]["processing"] == "job_expired"


@pytest.mark.parametrize("kind", ["file", "preview"])
def test_render_capacity_wait_survives_deadline_and_retries_with_delay(api, kind):  # noqa: F811
    from ailab_platform.models import FileObject

    response = api.post("/v1/files", headers=AUTH, files={"upload": ("wait.pdf", b"payload", "application/pdf")})
    fid = response.json()["id"]
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        if kind == "file":
            file.meta = {**(file.meta or {}), "processing": "render_pending", "scan": {"verdict": "clean"}}
            job = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == fid))
        else:
            file.status = "ready"
            job = jobs.stage_job(db, "preview", file_id=fid)
            file.meta = {**(file.meta or {}), "preview_job_id": job.id, "preview": {"status": "pending"}}
        job.payload = {**job.payload, "deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}
        job.deliveries = 8
        jid = job.id
        db.commit()
    claimed = claim(api, jid)
    assert claimed["state"] == "claimed"
    if kind == "file":
        assert claimed["attempt"] == 1
    response = api.post(f"/internal/jobs/{jid}/release", headers=SERVICE, json={"token": claimed["token"], "retry_after_seconds": 2})
    assert response.status_code == 200
    with SessionLocal() as db:
        job = db.get(OutboxMessage, jid)
        assert datetime.fromisoformat(job.payload["deadline"]) > datetime.now(UTC)
        when = job.next_attempt_at.replace(tzinfo=UTC) if job.next_attempt_at.tzinfo is None else job.next_attempt_at
        assert when > datetime.now(UTC)
        assert not (db.get(FileObject, fid).meta or {}).get("scan_exhausted")


def test_concurrent_thumbnail_backfills_share_artifact_job_identity(api):  # noqa: F811
    response = api.post("/v1/files", headers=AUTH, files={"upload": ("race.png", b"image", "image/png")})
    fid = response.json()["id"]
    assert api.patch(f"/internal/files/{fid}", headers=SERVICE, json={"status": "ready"}).status_code == 200

    def backfill(_):
        return api.post("/internal/jobs/thumbnails/backfill", headers=SERVICE, json={"limit": 1000})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(backfill, range(2)))
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    with SessionLocal() as db:
        rows = list(db.scalars(select(OutboxMessage).where(OutboxMessage.id == f"thumb_{fid}")))
        assert len(rows) == 1 and rows[0].payload["kind"] == "thumbnail"


def test_worker_scan_exhausted_counter_is_not_repeated_by_redelivery(api, monkeypatch):  # noqa: F811
    import threading

    from ailab_platform.ports.scanner import ScanResult
    from ailab_platform.queue import InMemoryQueue
    from ailab_platform.worker import main, pipeline
    from tests.test_file_previews import ApiClient

    response = api.post("/v1/files", headers=AUTH, files={"upload": ("scan.txt", b"payload", "text/plain")})
    fid = response.json()["id"]
    with SessionLocal() as db:
        job = db.scalar(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == fid))
        payload = job.payload

    class Client:
        def __init__(self, **kwargs):
            pass

        def job_action(self, job_id, action, **body):
            r = api.post(f"/internal/jobs/{job_id}/{action}", headers=SERVICE, json=body)
            assert r.status_code == 200, r.text
            return r.json()

        def close(self):
            pass

    class Down:
        def scan(self, stream):
            return ScanResult("unavailable", "clamav")

    class Counter:
        value = 0

        def inc(self):
            # The low-cardinality metric observes an already committed business flag.
            result = api.get(f"/v1/files/{fid}", headers=AUTH).json()
            assert result["meta"]["scan_exhausted"] is True
            self.value += 1

    counter = Counter()
    monkeypatch.setattr(main.metrics, "SCAN_EXHAUSTED", counter, raising=False)
    monkeypatch.setattr(main, "PlatformClient", Client)
    monkeypatch.setattr(main, "_stop", threading.Event())
    monkeypatch.setattr(main, "process_file", lambda file_id: pipeline.process_file(file_id, platform=ApiClient(api)))
    monkeypatch.setattr(pipeline, "get_scanner", lambda: Down())
    monkeypatch.setattr(settings, "scan_retry_backoff_seconds", 0.001)
    queue = InMemoryQueue()
    for _ in range(2):
        queue.enqueue(settings.file_queue, payload)
        main.handle_message(queue, queue.dequeue(settings.file_queue, timeout=0))
    assert counter.value == 1


def test_short_claim_renewal_is_token_fenced_and_cannot_revive_expiry(api, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "run_timeout_seconds", 86400)
    rid = start(api)
    first = claim(api, rid)
    with SessionLocal() as db:
        job = db.get(OutboxMessage, rid)
        assert (
            290
            < (
                (job.claimed_until.astimezone(UTC) if job.claimed_until.tzinfo else job.claimed_until.replace(tzinfo=UTC))
                - datetime.now(UTC)
            ).total_seconds()
            <= 300
        )
        job.claimed_until = datetime.now(UTC) + timedelta(seconds=1)
        db.commit()
    assert action(api, rid, "renew", "wrong").status_code == 409
    assert action(api, rid, "renew", first["token"]).status_code == 200
    with SessionLocal() as db:
        job = db.get(OutboxMessage, rid)
        assert (
            (job.claimed_until.astimezone(UTC) if job.claimed_until.tzinfo else job.claimed_until.replace(tzinfo=UTC)) - datetime.now(UTC)
        ).total_seconds() > 290
        assert job.deliveries == 1
        job.claimed_until = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert action(api, rid, "renew", first["token"]).status_code == 409
    assert claim(api, rid)["attempt"] == 2
    assert action(api, rid, "renew", first["token"]).status_code == 409


def test_renew_requires_job_service_scope(api, monkeypatch):  # noqa: F811
    from ailab_platform.ports.service_auth import ServicePrincipal
    from ailab_platform.service_auth import authenticator

    original = authenticator.authenticate
    monkeypatch.setattr(
        authenticator,
        "authenticate",
        lambda token: ServicePrincipal("worker", frozenset({"jobs:file"})) if token == "worker" else original(token),
    )
    rid = start(api)
    token = claim(api, rid)["token"]
    response = api.post(f"/internal/jobs/{rid}/renew", headers={"X-Service-Token": "worker"}, json={"token": token})
    assert response.status_code == 403


def test_budget_pause_exposes_saved_progress_and_resume_starts_new_window(api):  # noqa: F811
    from ailab_platform.adapters.runtime import fresh_state
    from tests.test_ledger_protocol import AUTH

    rid = start(api)
    token = claim(api, rid)["token"]
    headers = {**SERVICE, "X-Run-Attempt": token}
    response = api.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running", "attempt_id": token})
    assert response.status_code == 200, response.text
    state = fresh_state()
    state["plan"] = [{"id": "a", "title": "未完成", "status": "pending"}]
    response = api.put(f"/internal/runs/{rid}/checkpoint", headers=headers, json={"expected_step": 0, "state": state})
    assert response.status_code == 200, response.text
    reason = "已工作4小时，已暂停，可继续"
    response = api.post(
        f"/internal/runs/{rid}/pause",
        headers=headers,
        json={
            "status": "interrupted",
            "error": reason,
            "summary": "部分完成",
            "result": {"workspace_saved": True, "pause": {"kind": "budget"}},
        },
    )
    assert response.status_code == 200, response.text
    detail = api.get(f"/v1/runs/{rid}", headers=AUTH).json()
    assert detail["resumable"] and detail["error"] == reason and detail["summary"] == "部分完成"
    assert detail["result"]["workspace_saved"] and detail["result"]["plan"] == state["plan"]
    response = api.post(f"/v1/runs/{rid}/resume", headers=AUTH)
    assert response.status_code == 200, response.text
    next_window = api.get(f"/internal/runs/{rid}", headers=SERVICE).json()["budget_window_id"]
    assert next_window != rid
    assert action(api, rid, "ack", token).status_code == 200
    with SessionLocal() as db:
        assert db.get(OutboxMessage, next_window) is not None
