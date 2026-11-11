"""A committed outbox insert wakes the dispatcher instead of waiting for the poll."""

import asyncio
import contextlib
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from ailab_platform.api import jobs, preview_dispatch
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.models import OutboxMessage
from ailab_platform.queue import get_queue


@pytest.fixture(scope="module", autouse=True)
def migrated():
    init_db()


@pytest.fixture()
def rows():
    created = []
    yield created
    with SessionLocal() as db:
        db.execute(delete(OutboxMessage).where(OutboxMessage.id.in_(created)))
        db.commit()


def staged(rows, topic="test"):
    job_id = uuid4().hex
    rows.append(job_id)
    return OutboxMessage(id=job_id, topic=topic, payload={"job_id": job_id, "kind": "run"})


def test_commit_of_a_staged_job_wakes_once(rows, monkeypatch):
    wakes = []
    monkeypatch.setattr(jobs, "wake_dispatch", lambda: wakes.append(1))

    with SessionLocal() as db:
        db.add(staged(rows))
        db.rollback()
    assert wakes == []  # a rolled back job was never published

    with SessionLocal() as db:
        db.commit()
    assert wakes == []  # unrelated commits stay quiet

    with SessionLocal() as db:
        db.add(staged(rows))
        assert wakes == []
        db.commit()
    assert wakes == [1]


def test_savepoint_commit_waits_for_the_outer_transaction(rows, monkeypatch):
    wakes = []
    monkeypatch.setattr(jobs, "wake_dispatch", lambda: wakes.append(1))

    with SessionLocal() as db:
        nested = db.begin_nested()
        db.add(staged(rows))
        nested.commit()
        assert wakes == []  # the row is not visible to the dispatcher yet
        db.commit()
    assert wakes == [1]


def test_wake_dispatch_signals_the_bound_loop_and_is_inert_without_one(monkeypatch):
    wakeup = asyncio.Event()

    class Loop:
        def is_closed(self):
            return False

        def call_soon_threadsafe(self, callback):
            callback()

    monkeypatch.setattr(preview_dispatch, "_serving_loop", Loop())
    monkeypatch.setattr(preview_dispatch, "_dispatch_wakeup", wakeup)
    preview_dispatch.wake_dispatch()
    assert wakeup.is_set()

    monkeypatch.setattr(preview_dispatch, "_serving_loop", None)
    preview_dispatch.wake_dispatch()  # startup and non-API processes must not raise


def test_wake_short_circuits_the_poll_interval(rows, monkeypatch):
    from ailab_platform.api import file_dependencies

    async def scenario():
        passes = 0
        woken = asyncio.Event()

        def dispatch(job_id=None):
            nonlocal passes
            passes += 1
            if passes >= 2:
                woken.set()
            return 0

        monkeypatch.setattr(preview_dispatch, "dispatch_preview_jobs", dispatch)
        monkeypatch.setattr(file_dependencies, "reconcile", lambda: None)
        # Long enough that a second pass can only come from the wake, never from the poll.
        monkeypatch.setattr(preview_dispatch, "OUTBOX_POLL_SECONDS", 30.0)
        task = asyncio.create_task(preview_dispatch.dispatch_forever())
        try:
            for _ in range(500):
                if passes:
                    break
                await asyncio.sleep(0.01)
            assert passes == 1
            preview_dispatch.wake_dispatch()
            await asyncio.wait_for(woken.wait(), 1.0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        assert preview_dispatch._serving_loop is None and preview_dispatch._dispatch_wakeup is None

    asyncio.run(scenario())


def test_run_creation_is_published_without_waiting_for_the_poll(monkeypatch):
    # The interval is far longer than the assertion, so only the commit wake can publish the job in time.
    monkeypatch.setattr(preview_dispatch, "OUTBOX_POLL_SECONDS", 30.0)
    from ailab_platform.api.main import app

    with TestClient(app) as client:
        created = client.post("/v1/runs", headers={"Authorization": "Bearer test-token"}, json={"prompt": "wake", "model": "test-fast"})
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        topic = f"{settings.run_queue}:work"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if any(job.get("run_id") == run_id for job in get_queue().outstanding(topic)):
                break
            time.sleep(0.01)
        else:
            pytest.fail("run job waited for the poll instead of the commit wake")
