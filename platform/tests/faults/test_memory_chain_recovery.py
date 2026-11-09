"""Expired capacity and crash/redelivery regressions for the memory job protocol."""

import asyncio
from datetime import timedelta

from sqlalchemy import select

from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.memory_dream.scheduling import can_start
from ailab_platform.models import MemoryDreamRun, PlatformSetting, User, utcnow


def test_crashed_lease_expires_without_raising_capacity(monkeypatch):
    monkeypatch.setattr(service.settings, "memory_dream_concurrency", 1)
    with SessionLocal() as db:
        users = [User(email=f"lease-{utcnow().timestamp()}-{i}@test.invalid") for i in range(2)]
        db.add_all(users)
        db.flush()
        crashed = MemoryDreamRun(
            user_id=users[0].id, local_date="2026-11-09", status="running", claimed_until=utcnow() + timedelta(minutes=1)
        )
        waiting = MemoryDreamRun(user_id=users[1].id, local_date="2026-11-09", trigger="manual")
        db.add_all([crashed, waiting])
        db.flush()
        scheduler = PlatformSetting(key="test-memory-capacity", value={})
        assert not can_start(db, scheduler, waiting, utcnow())
        assert can_start(db, scheduler, waiting, utcnow() + timedelta(minutes=2))
        db.rollback()


def test_chain_recovery_follows_committed_successor_once(monkeypatch):
    monkeypatch.setattr(service.settings, "memory_dream_manual_batches", 2)
    with SessionLocal() as db:
        user = User(email=f"chain-{utcnow().timestamp()}@test.invalid")
        db.add(user)
        db.flush()
        first = MemoryDreamRun(
            user_id=user.id,
            local_date="2026-11-09",
            trigger="manual",
            status="applied",
            batch_index=1,
            report={"chain_running": True, "runs_seen": 3, "added": 1, "stage_committed": True},
        )
        db.add(first)
        db.commit()
        first_id = first.id
    monkeypatch.setattr(service, "_new_runs", lambda *a, **kw: [object()])
    calls = []

    async def process(run_id):
        with SessionLocal() as db:
            row = db.get(MemoryDreamRun, run_id)
            if row.status == "pending":
                calls.append(run_id)
                row.status = "applied"
                row.report = {**(row.report or {}), "runs_seen": 2, "added": 1, "stage_committed": True, "chain_running": True}
                db.commit()

    monkeypatch.setattr(service, "process_dream", process)
    asyncio.run(service.process_dream_chain(first_id))
    asyncio.run(service.process_dream_chain(first_id))
    with SessionLocal() as db:
        first = db.get(MemoryDreamRun, first_id)
        last = db.get(MemoryDreamRun, first.report["next_id"])
        assert calls == [last.id]
        assert len(list(db.scalars(select(MemoryDreamRun).where(MemoryDreamRun.user_id == first.user_id)))) == 2
        report = service._run_out(last)
        assert report.status == "applied"
        assert report.report["runs_seen"] == 5 and report.report["added"] == 2
        assert report.report["chain_complete"] and not report.report["chain_running"]
