"""Nightly cadence: one cron chain per local date inside the window, a persisted cutoff and deadline, no parked successors."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.models import MemoryDreamRun, MemoryWorkItem, Run, User, new_id, utcnow

# 2026-11-10 03:00 UTC; the user's dream timezone is UTC so the window is 02:00-05:00 UTC.
NIGHT = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)


@pytest.fixture(scope="module", autouse=True)
def isolated_database():
    from ailab_platform.db import init_db

    init_db()


@pytest.fixture(autouse=True)
def no_stagger(monkeypatch):
    monkeypatch.setattr("ailab_platform.memory_dream.scheduling.offset", lambda *args: 0)


def _user(db, runs=1, finished=None):
    user = User(email=f"night-{new_id('u')}@test.invalid", settings={"memory_dream": {"timezone": "UTC"}})
    db.add(user)
    db.flush()
    for index in range(runs):
        at = finished or NIGHT - timedelta(hours=5, minutes=index)
        db.add(Run(id=new_id("run"), user_id=user.id, prompt=f"p{index}", status="succeeded", created_at=at, finished_at=at))
    db.commit()
    return user.id


def _claim(monkeypatch, uid, now):
    monkeypatch.setattr(service, "_candidate_users", lambda db, now: [db.get(User, uid)])
    return service.claim_due(now=now)


def _cron(db, uid):
    return list(db.scalars(select(MemoryDreamRun).where(MemoryDreamRun.user_id == uid, MemoryDreamRun.trigger == "cron")))


def test_claim_only_inside_the_window_and_once_per_local_date(monkeypatch):
    with SessionLocal() as db:
        uid = _user(db)
    assert _claim(monkeypatch, uid, NIGHT.replace(hour=1, minute=59)) == []
    assert _claim(monkeypatch, uid, NIGHT.replace(hour=5, minute=0)) == []  # window_end is exclusive
    assert _claim(monkeypatch, uid, NIGHT.replace(hour=14)) == []  # daytime never starts a chain
    claimed = _claim(monkeypatch, uid, NIGHT)
    assert len(claimed) == 1
    with SessionLocal() as db:
        row = db.get(MemoryDreamRun, claimed[0])
        assert row.report["cutoff_at"] == NIGHT.isoformat()
        row.status, row.report = "failed", {**row.report, "chain_complete": True}
        db.commit()
    # failed, finished or voided: still tonight's chain
    assert _claim(monkeypatch, uid, NIGHT + timedelta(minutes=30)) == []
    assert len(_claim(monkeypatch, uid, NIGHT + timedelta(days=1))) == 1


def test_manual_dream_does_not_use_the_nightly_slot(monkeypatch):
    with SessionLocal() as db:
        uid = _user(db)
        db.add(MemoryDreamRun(user_id=uid, local_date="2026-11-10", trigger="manual", status="applied", report={"chain_complete": True}))
        db.commit()
    assert len(_claim(monkeypatch, uid, NIGHT)) == 1


def test_chain_stops_at_batch_limit_without_parking_a_successor(monkeypatch):
    monkeypatch.setattr(service.settings, "memory_dream_auto_batches", 2)
    with SessionLocal() as db:
        uid = _user(db, runs=5)
    first = _claim(monkeypatch, uid, NIGHT)[0]
    processed = []

    async def process(run_id):
        processed.append(run_id)
        with SessionLocal() as db:
            row = db.get(MemoryDreamRun, run_id)
            pending = service._new_runs(db, uid, None, utcnow(), limit=1, cutoff=service._cutoff(row))
            db.add(MemoryWorkItem(run_id=pending[0].id, user_id=uid, state="done", candidates=[]))
            row.status = "applied"
            row.report = {**row.report, "stage_committed": True, "chain_running": True, "runs_seen": 1}
            db.commit()

    monkeypatch.setattr(service, "process_dream", process)
    asyncio.run(service.process_dream_chain(first))
    with SessionLocal() as db:
        rows = _cron(db, uid)
        assert len(processed) == 2 and len(rows) == 2
        assert all(r.status == "applied" for r in rows)
        assert {r.report["cutoff_at"] for r in rows} == {NIGHT.isoformat()}
        # three conversations stay ready for the next night; nothing runs during the day
        assert len(service._new_runs(db, uid, None, utcnow())) == 3
    assert not [r for r in service.recover_pending(NIGHT.replace(hour=14)) if r in {x.id for x in rows}]


def test_turns_that_end_after_the_cutoff_wait_for_the_next_night(monkeypatch):
    with SessionLocal() as db:
        uid = _user(db, runs=1)
        late = Run(id=new_id("run"), user_id=uid, prompt="late", status="succeeded", created_at=NIGHT - timedelta(minutes=5))
        late.finished_at = NIGHT + timedelta(minutes=5)
        db.add(late)
        db.commit()
        late_id = late.id
    first = _claim(monkeypatch, uid, NIGHT)[0]
    with SessionLocal() as db:
        job = db.get(MemoryDreamRun, first)
        assert late_id not in job.input_run_ids
        # after a restart the persisted cutoff, not the clock, decides
        runs = service._new_runs(db, uid, None, utcnow(), cutoff=service._cutoff(job))
        assert late_id not in {r.id for r in runs} and len(runs) == 1
        assert late_id in {r.id for r in service._new_runs(db, uid, None, utcnow())}


def _never_called(monkeypatch):
    async def unexpected(*args):
        raise AssertionError("no model call after the deadline or the window")

    monkeypatch.setattr(service, "_chat", unexpected)


def test_deadline_fails_the_next_batch_without_a_model_call(monkeypatch):
    _never_called(monkeypatch)
    with SessionLocal() as db:
        uid = _user(db)
        past = utcnow() - timedelta(minutes=1)
        job = MemoryDreamRun(
            user_id=uid,
            local_date="2026-11-10",
            timezone="UTC",
            trigger="cron",
            batch_index=2,
            due_at=past,
            report={
                "cutoff_at": past.isoformat(),
                "chain_started_at": (past - timedelta(minutes=30)).isoformat(),
                "deadline": past.isoformat(),
            },
        )
        db.add(job)
        db.commit()
        identity = job.id
    asyncio.run(service.process_dream(identity))
    with SessionLocal() as db:
        row = db.get(MemoryDreamRun, identity)
        assert row.status == "failed" and row.report["reason"] == "timeout"
        assert row.report["deadline"] == past.isoformat()


def test_deadline_stops_the_chain_after_a_finished_batch(monkeypatch):
    with SessionLocal() as db:
        uid = _user(db, runs=3)
        past = utcnow() - timedelta(minutes=1)
        job = MemoryDreamRun(
            user_id=uid,
            local_date="2026-11-10",
            timezone="UTC",
            trigger="cron",
            status="applied",
            report={"cutoff_at": utcnow().isoformat(), "deadline": past.isoformat(), "chain_running": True, "stage_committed": True},
        )
        db.add(job)
        db.commit()
        identity = job.id

    async def done(run_id):
        return None

    monkeypatch.setattr(service, "process_dream", done)
    asyncio.run(service.process_dream_chain(identity))
    with SessionLocal() as db:
        row = db.get(MemoryDreamRun, identity)
        assert row.report["chain_complete"] and row.report["reason"] == "timeout" and not row.report.get("next_id")
        assert len(_cron(db, uid)) == 1


def test_chain_admitted_after_the_window_is_void_but_counts(monkeypatch):
    _never_called(monkeypatch)
    with SessionLocal() as db:
        uid = _user(db)
    first = _claim(monkeypatch, uid, NIGHT.replace(hour=4, minute=59))[0]
    monkeypatch.setattr(service, "utcnow", lambda: NIGHT.replace(hour=5, minute=1))
    asyncio.run(service.process_dream(first))
    with SessionLocal() as db:
        row = db.get(MemoryDreamRun, first)
        assert row.status == "skipped" and row.report["reason"] == "late"
        assert not row.report.get("chain_started_at")
    assert _claim(monkeypatch, uid, NIGHT.replace(hour=4, minute=59) + timedelta(seconds=30)) == []


def test_first_batch_records_chain_start_and_deadline(monkeypatch):
    monkeypatch.setattr(service.settings, "memory_dream_chain_timeout_seconds", 1800)
    with SessionLocal() as db:
        uid = _user(db)
    first = _claim(monkeypatch, uid, NIGHT)[0]
    monkeypatch.setattr(service, "utcnow", lambda: NIGHT + timedelta(minutes=1))
    seen = {}

    async def chat(messages, model, user):
        with SessionLocal() as db:
            seen.update(db.get(MemoryDreamRun, first).report)
        return json.dumps({"ops": []}), {}

    monkeypatch.setattr(service, "_chat", chat)
    asyncio.run(service.process_dream(first))
    started = NIGHT + timedelta(minutes=1)
    assert seen["chain_started_at"] == started.isoformat()
    assert seen["deadline"] == (started + timedelta(seconds=1800)).isoformat()
    with SessionLocal() as db:
        report = db.get(MemoryDreamRun, first).report
        assert report["deadline"] == seen["deadline"] and report["cutoff_at"] == NIGHT.isoformat()


def test_chain_timeout_setting_is_bounded():
    from pydantic import ValidationError

    from ailab_platform.config import Settings

    assert Settings(memory_dream_chain_timeout_seconds=60).memory_dream_chain_timeout_seconds == 60
    for value in (59, 7201):
        with pytest.raises(ValidationError):
            Settings(memory_dream_chain_timeout_seconds=value)
