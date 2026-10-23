"""Durable side effects survive lost publication and transport state."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ailab_platform.api import main as api_main
from ailab_platform.api.preview_dispatch import dispatch_preview_jobs
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import OutboxMessage
from ailab_platform.queue import get_queue
from tests.test_ledger_protocol import AUTH


@pytest.fixture
def api(monkeypatch):
    import asyncio

    async def idle_dispatch():
        await asyncio.Event().wait()

    # Drive recovery ticks explicitly: an unrelated background tick must not win assertions.
    monkeypatch.setattr(api_main, "dispatch_forever", idle_dispatch)
    with TestClient(api_main.app) as client:
        yield client


def test_run_commit_survives_publication_failure(api, monkeypatch):
    queue = get_queue()
    monkeypatch.setattr(queue, "enqueue", lambda *a: (_ for _ in ()).throw(ConnectionError("offline")))
    response = api.post("/v1/runs", headers=AUTH, json={"prompt": "durable", "model": "test-fast"})
    assert response.status_code == 201, response.text
    with SessionLocal() as db:
        job = db.get(OutboxMessage, response.json()["id"])
        assert job.payload["run_id"] == response.json()["id"]
        assert job.payload["schema_version"] == 1


def test_sent_job_retained_until_ack_and_replayed(api, monkeypatch):
    from ailab_platform.api.jobs import stage_job
    from ailab_platform.queue import InMemoryQueue

    queue = InMemoryQueue()
    monkeypatch.setattr("ailab_platform.api.preview_dispatch.get_queue", lambda: queue)

    with SessionLocal() as db:
        job = stage_job(db, "file", file_id="missing")
        db.commit()
        job_id = job.id
    now = datetime.now(UTC)
    assert dispatch_preview_jobs(job_id, now=now) == 1
    with SessionLocal() as db:
        assert db.get(OutboxMessage, job_id) is not None
    queue.ack(settings.file_queue, queue.dequeue(settings.file_queue, timeout=0))
    assert dispatch_preview_jobs(job_id, now=now + timedelta(seconds=61)) == 1
    assert queue.dequeue(settings.file_queue, timeout=0).payload["job_id"] == job_id


def test_sigkill_after_database_commit_is_recovered(api):
    import os
    import subprocess
    import sys
    from uuid import uuid4

    job_id = uuid4().hex
    program = """
import os, signal
from ailab_platform.db import SessionLocal
from ailab_platform.api.jobs import stage_job
with SessionLocal() as db:
    stage_job(db, "file", file_id="killed-process", job_id=os.environ["FAULT_JOB_ID"])
    db.commit()
os.kill(os.getpid(), signal.SIGKILL)
"""
    result = subprocess.run([sys.executable, "-c", program], env={**os.environ, "FAULT_JOB_ID": job_id}, timeout=15)
    assert result.returncode == -9
    assert dispatch_preview_jobs(job_id) == 1
    assert any(p.get("job_id") == job_id for p in get_queue().outstanding(settings.file_queue))


def test_publication_backoff_and_manual_attention(api, monkeypatch):
    from ailab_platform.api.jobs import stage_job

    queue = get_queue()
    with SessionLocal() as db:
        job = stage_job(db, "file", file_id="failure")
        db.commit()
        job_id = job.id
    calls = []

    def unavailable(*args):
        calls.append(args)
        raise ConnectionError("secret-must-not-be-recorded")

    monkeypatch.setattr(queue, "enqueue", unavailable)
    monkeypatch.setattr(settings, "outbox_max_attempts", 2)
    now = datetime.now(UTC)
    assert dispatch_preview_jobs(job_id, now=now) == 0
    assert dispatch_preview_jobs(job_id, now=now + timedelta(seconds=1)) == 0
    assert len(calls) == 1
    assert dispatch_preview_jobs(job_id, now=now + timedelta(seconds=3)) == 0
    with SessionLocal() as db:
        job = db.get(OutboxMessage, job_id)
        assert job.attempts == 2 and job.needs_attention == 1
        assert job.last_error == "ConnectionError"
    dispatch_preview_jobs(job_id, now=now + timedelta(days=1))
    assert len(calls) == 2


def test_real_redis_state_loss_replays_same_job_identity(api, monkeypatch):
    import os
    from uuid import uuid4

    import pytest

    from ailab_platform.api import preview_dispatch
    from ailab_platform.api.jobs import stage_job
    from ailab_platform.queue import RedisQueue

    url = os.environ.get("AILAB_TEST_REDIS_URL")
    if not url:
        pytest.skip("AILAB_TEST_REDIS_URL not set")
    queue = RedisQueue(url)
    stream = "fault:" + uuid4().hex
    monkeypatch.setattr(settings, "file_queue", stream)
    monkeypatch.setattr(preview_dispatch, "get_queue", lambda: queue)
    try:
        with SessionLocal() as db:
            job = stage_job(db, "file", file_id="redis-loss")
            db.commit()
            job_id = job.id
        now = datetime.now(UTC)
        assert dispatch_preview_jobs(job_id, now=now) == 1
        first = queue.dequeue(stream, timeout=0)
        # Redis stream plus pending/consumer group state disappear together.
        queue.client.delete(stream)
        assert dispatch_preview_jobs(job_id, now=now + timedelta(seconds=61)) == 1
        again = queue.dequeue(stream, timeout=0)
        assert again.payload == first.payload
        assert again.payload["job_id"] == job_id
    finally:
        queue.client.delete(stream)
        queue.client.close()


def test_sigterm_releases_durable_job_before_exit(api):
    import json
    import os
    import select
    import signal
    import subprocess
    import sys
    from pathlib import Path

    from tests.test_transactional_jobs import claim, start

    run_ids = [start(api, "work"), start(api, "work"), start(api, "chat")]
    with SessionLocal() as db:
        payloads = [db.get(OutboxMessage, rid).payload for rid in run_ids]
    child = Path(__file__).with_name("agent_child.py")
    process = subprocess.Popen(
        [sys.executable, str(child)],
        env={**os.environ, "FAULT_ENVELOPES": json.dumps(payloads)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stdout], [], [], 15)[0], "child did not acquire job"
        assert process.stdout.readline().strip() == "started"
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stderr
        assert "drained" in stdout
        for rid in run_ids:
            with SessionLocal() as db:
                job = db.get(OutboxMessage, rid)
                assert job.claim_token is None and job.claimed_until is None
            assert claim(api, rid)["attempt"] == 2
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_dispatch_snapshot_cannot_shorten_concurrent_claim(api, monkeypatch):
    from ailab_platform.api import preview_dispatch
    from tests.test_transactional_jobs import claim, start

    rid = start(api)
    queue = get_queue()
    enqueue = queue.enqueue
    owned = []

    def claim_during_publication(name, payload):
        # Real PostgreSQL row locks serialize this case; SQLite is where the CAS is essential.
        from ailab_platform.db import engine

        if engine.dialect.name == "postgresql":
            import pytest

            pytest.skip("PG dispatcher FOR UPDATE prevents the concurrent claim until commit")
        owned.append(claim(api, rid))
        enqueue(name, payload)

    monkeypatch.setattr(queue, "enqueue", claim_during_publication)
    now = datetime.now(UTC)
    assert preview_dispatch.dispatch_preview_jobs(rid, now=now) == 1
    with SessionLocal() as db:
        job = db.get(OutboxMessage, rid)
        until = job.claimed_until.replace(tzinfo=UTC) if job.claimed_until.tzinfo is None else job.claimed_until
        next_at = job.next_attempt_at.replace(tzinfo=UTC) if job.next_attempt_at.tzinfo is None else job.next_attempt_at
        assert until == next_at and until > now + timedelta(seconds=60)
        assert job.claim_token == owned[0]["token"]
    assert preview_dispatch.dispatch_preview_jobs(rid, now=now + timedelta(seconds=61)) == 0
