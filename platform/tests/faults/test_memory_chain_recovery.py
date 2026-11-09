"""Expired capacity and crash/redelivery regressions for the memory job protocol."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.memory_dream.scheduling import can_start
from ailab_platform.models import MemoryDreamRun, PlatformSetting, User, utcnow


@pytest.fixture(scope="module", autouse=True)
def isolated_database():
    from ailab_platform.db import init_db

    init_db()


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


def test_database_admission_serializes_independent_connections(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from ailab_platform.memory_dream.scheduling import admission

    monkeypatch.setattr(service.settings, "memory_dream_concurrency", 3)
    now = utcnow()
    with SessionLocal() as db:
        users = [User(email=f"multi-admission-{now.timestamp()}-{i}@test.invalid") for i in range(8)]
        db.add_all(users)
        db.flush()
        jobs = [MemoryDreamRun(user_id=u.id, local_date="2026-11-09", trigger="manual") for u in users]
        db.add_all(jobs)
        db.commit()
        ids = [j.id for j in jobs]

    def claim(key):
        with SessionLocal() as db, admission(db) as scheduler:
            job = db.get(MemoryDreamRun, key)
            accepted = can_start(db, scheduler, job, now)
            if accepted:
                job.claimed_until = now + timedelta(minutes=10)
                job.status = "running"
            db.commit()
            return accepted

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(claim, ids)) == 3


def test_crash_after_candidate_commit_resumes_without_duplicate_items(monkeypatch):
    import os
    import subprocess
    import sys

    from sqlalchemy import update

    from ailab_platform.memory_dream import consolidation
    from ailab_platform.models import MemoryItem, MemoryWorkItem, Run

    now = utcnow()
    with SessionLocal() as db:
        user = User(email=f"crash-candidate-{now.timestamp()}@test.invalid")
        db.add(user)
        db.flush()
        run = Run(user_id=user.id, prompt="我使用PostgreSQL", model="local-demo", status="succeeded")
        job = MemoryDreamRun(
            user_id=user.id,
            trigger="manual",
            local_date="2026-11-09",
            created_at=now - timedelta(minutes=11),
            due_at=now - timedelta(minutes=11),
        )
        db.add_all([run, job])
        db.commit()
        job_id, run_id, uid = job.id, run.id, user.id
    script = """
import asyncio, os
from ailab_platform.memory_dream import service
async def extract(episodes, active, user):
 e=episodes[0]
 return ([{'text':'用户使用PostgreSQL','kind':'fact','entities':['PostgreSQL'], 'scope':'account',
           'evidence_run_ids':[e.run_id],'evidence_episode_ids':[e.id],'source_quote':'我使用PostgreSQL','confidence':1}],{},'local-demo')
async def crash(*args): os._exit(91)
service._extract=extract
service._judge=crash
service.settings.memory_dream_min_runs=1
asyncio.run(service.process_dream(os.environ['MEMORY_FAULT_JOB']))
"""
    result = subprocess.run([sys.executable, "-c", script], env={**os.environ, "MEMORY_FAULT_JOB": job_id}, capture_output=True, timeout=40)
    assert result.returncode == 91, result.stderr.decode()[-1000:]
    with SessionLocal() as db:
        assert db.get(MemoryWorkItem, run_id).candidates
        assert not list(db.scalars(select(MemoryItem).where(MemoryItem.user_id == uid)))
        db.execute(update(MemoryDreamRun).where(MemoryDreamRun.id == job_id).values(claimed_until=now - timedelta(seconds=1)))
        db.commit()
    assert job_id in service.recover_pending(now)

    async def judge(facts, active, user, model):
        return {i: {"supported": True} for i in range(len(facts))}, {}

    async def unexpected(*args):
        raise AssertionError("committed candidates must not be extracted twice")

    async def no_consolidation(*args):
        return None

    monkeypatch.setattr(service, "_extract", unexpected)
    monkeypatch.setattr(service, "_judge", judge)
    monkeypatch.setattr(consolidation, "consolidate", no_consolidation)
    monkeypatch.setattr(service.settings, "memory_dream_min_runs", 1)
    asyncio.run(service.process_dream(job_id))
    asyncio.run(service.process_dream(job_id))
    with SessionLocal() as db:
        rows = list(db.scalars(select(MemoryItem).where(MemoryItem.user_id == uid)))
        assert len(rows) == 1 and rows[0].source_run_ids == [run_id]
        assert db.get(MemoryDreamRun, job_id).status == "applied"


def test_soft_deadline_persists_oldest_due_for_next_dispatch(monkeypatch):
    from ailab_platform.memory_recall.store import stamp

    now = utcnow()
    with SessionLocal() as db:
        user = User(email=f"carry-{now.timestamp()}@test.invalid")
        db.add(user)
        db.flush()
        job = MemoryDreamRun(
            user_id=user.id,
            local_date=now.date().isoformat(),
            timezone="UTC",
            trigger="cron",
            status="applied",
            due_at=now - timedelta(hours=3),
            priority=20,
            report={"chain_running": True, "stage_committed": True, "runs_seen": 1, "added": 1},
        )
        db.add(job)
        db.commit()
        identity, due = job.id, stamp(job.due_at)

    async def process(*args):
        return None

    monkeypatch.setattr(service, "process_dream", process)
    monkeypatch.setattr(service, "_new_runs", lambda *a, **kw: [object()])
    monkeypatch.setattr(service, "local_window", lambda ds: (0, 1))
    asyncio.run(service.process_dream_chain(identity))
    with SessionLocal() as db:
        first = db.get(MemoryDreamRun, identity)
        successor = db.get(MemoryDreamRun, first.report["next_id"])
        assert successor.status == "pending" and successor.priority == 20
        assert stamp(successor.due_at) == due
        assert successor.report["runs_seen_before"] == 1
