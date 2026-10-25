"""Bounded lifecycle collectors, durable retries and an upload/GC interleaving."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from ailab_platform.api.reaper import (
    _gc_cursors,
    drain_object_deletions,
    lifecycle_once,
    purge_deleted_conversations,
    purge_expired_presigns,
    purge_expired_workspaces,
    purge_orphan_objects,
    purge_run_events,
)
from ailab_platform.config import settings
from ailab_platform.locks import user_critical_section
from ailab_platform.migrate import run_migrations
from ailab_platform.models import Conversation, FileObject, LifecycleDeletion, Run, RunEvent, User
from ailab_platform.storage import LocalStorage

NOW = datetime.now(UTC)
OLD = NOW - timedelta(days=40)


@pytest.fixture
def env(tmp_path, monkeypatch, engine):
    run_migrations(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("ailab_platform.api.reaper.SessionLocal", factory)
    with factory() as db:
        db.add(User(id="u", email="gc@example.test"))
        db.commit()
    _gc_cursors.clear()
    monkeypatch.setattr(settings, "gc_batch_size", 2)
    yield factory, LocalStorage(tmp_path / "storage")
    engine.dispose()
    _gc_cursors.clear()


def put_old(storage, key):
    storage.put_bytes(key, b"x")
    os.utime(storage.root / key, (OLD.timestamp(), OLD.timestamp()))


def test_presign_targets_and_idempotency(env):
    factory, storage = env
    with factory() as db:
        for id, key, meta in [
            ("old", "uploads/u/old/x", {"processing": "awaiting_upload"}),
            ("new", "uploads/u/new/x", {"processing": "awaiting_upload"}),
            ("done", "uploads/u/done/obj/x", {"processing": "queued"}),
        ]:
            db.add(FileObject(id=id, user_id="u", filename=f"x-{id}", storage_key=key, meta=meta, created_at=NOW if id == "new" else OLD))
            put_old(storage, key)
        db.commit()
        for _ in range(3):
            purge_expired_presigns(db, storage, NOW)
            drain_object_deletions(db, storage)
        assert db.get(FileObject, "old") is None
        assert db.get(FileObject, "new") is not None and db.get(FileObject, "done") is not None
        assert not storage.exists("uploads/u/old/x")
        assert storage.exists("uploads/u/new/x") and storage.exists("uploads/u/done/obj/x")
        assert purge_expired_presigns(db, storage, NOW) == 0


def test_orphans_keep_live_owners_and_young_objects(env):
    factory, storage = env
    with factory() as db:
        db.add(FileObject(id="live", user_id="u", filename="x", storage_key="kept"))
        db.add(Run(id="live", user_id="u", prompt="p"))
        db.commit()
        for prefix in ("derived", "previews"):
            put_old(storage, f"{prefix}/gone/x")
            put_old(storage, f"{prefix}/live/x")
            storage.put_bytes(f"{prefix}/young/x", b"x")
        for _ in range(4):
            purge_orphan_objects(db, storage, NOW)
            drain_object_deletions(db, storage)
        for prefix in ("derived", "previews"):
            assert not storage.exists(f"{prefix}/gone/x")
            assert storage.exists(f"{prefix}/live/x") and storage.exists(f"{prefix}/young/x")
        assert purge_orphan_objects(db, storage, NOW) == 0


def test_events_finished_only_and_keep_summary(env):
    factory, _ = env
    with factory() as db:
        for id, status, finished in [("old", "succeeded", OLD), ("active", "running", None), ("new", "failed", NOW)]:
            db.add(Run(id=id, user_id="u", prompt="p", summary="keep", status=status, finished_at=finished))
            db.flush()
            db.add(RunEvent(run_id=id, seq=1, type="x", created_at=OLD))
        db.commit()
        assert purge_run_events(db, NOW) == 1
        assert purge_run_events(db, NOW) == 0
        assert db.get(Run, "old").summary == "keep"
        assert len(db.scalars(select(RunEvent)).all()) == 2


def test_conversation_purge_batches_and_retries_objects(env, monkeypatch):
    factory, storage = env
    with factory() as db:
        for id, deleted in [("old", OLD), ("new", NOW), ("live", None)]:
            db.add(Conversation(id=id, user_id="u", deleted_at=deleted))
        db.flush()
        for i in range(3):
            db.add(Run(id=f"r{i}", user_id="u", conversation_id="old", prompt="p", status="succeeded", finished_at=OLD))
            db.flush()
            db.add(RunEvent(run_id=f"r{i}", seq=1, type="x"))
            put_old(storage, f"workspaces/r{i}.tgz")
            put_old(storage, f"workspaces/r{i}/snap.tgz")
            put_old(storage, f"checkpoints/r{i}/1.json.gz")
            put_old(storage, f"previews/r{i}/1/x")
        db.commit()
        assert purge_deleted_conversations(db, NOW) == 0
        assert len(db.scalars(select(Run)).all()) == 3
        purged = 0
        for _ in range(5):
            purged += purge_deleted_conversations(db, NOW)
        assert purged == 1
        original = storage.delete
        monkeypatch.setattr(storage, "delete", lambda *_: (_ for _ in ()).throw(OSError("storage offline")))
        assert drain_object_deletions(db, storage) == 0
        assert db.scalars(select(LifecycleDeletion)).first() is not None
        monkeypatch.setattr(storage, "delete", original)
        for _ in range(8):
            drain_object_deletions(db, storage)
        assert db.scalars(select(LifecycleDeletion)).first() is None
        assert db.get(Conversation, "new") and db.get(Conversation, "live")
        assert not storage.list_prefix("workspaces/").items and not storage.list_prefix("previews/").items
        assert purge_deleted_conversations(db, NOW) == 0


def test_conversation_purge_keeps_the_usage_ledger(env):
    """What a purged conversation's runs cost still counts toward quota and reports."""
    from ailab_platform.models import ModelUsage
    from ailab_platform.quota import tokens_used_since

    factory, _ = env
    with factory() as db:
        db.add(Conversation(id="gone", user_id="u", deleted_at=OLD))
        db.flush()
        db.add(Run(id="paid", user_id="u", conversation_id="gone", prompt="p", status="succeeded", finished_at=OLD))
        db.flush()
        db.add(ModelUsage(run_id="paid", call_id="call-1", user_id="u", weighted_amount=12_000, status="settled", created_at=OLD))
        db.commit()
        before = tokens_used_since(db, "u", OLD - timedelta(days=1))
        for _ in range(3):
            purge_deleted_conversations(db, NOW)
        assert db.get(Run, "paid") is None
        assert db.scalars(select(ModelUsage).where(ModelUsage.call_id == "call-1")).one().run_id == "paid"
        assert tokens_used_since(db, "u", OLD - timedelta(days=1)) == before > 0


def test_gc_waiting_for_upload_observes_frozen_object(env, monkeypatch):
    factory, storage = env
    key, frozen = "uploads/u/file/x", "uploads/u/file/obj/x"
    put_old(storage, key)
    with factory() as db:
        db.add(FileObject(id="file", user_id="u", filename="x", storage_key=key, meta={"processing": "awaiting_upload"}, created_at=OLD))
        db.commit()
    locked = threading.Event()
    finish = threading.Event()
    gc_waiting = threading.Event()
    from contextlib import contextmanager

    from ailab_platform import locks

    @contextmanager
    def observed_lock(db, user_id):
        gc_waiting.set()
        with user_critical_section(db, user_id):
            yield

    monkeypatch.setattr(locks, "user_critical_section", observed_lock)

    def upload():
        with factory() as db, user_critical_section(db, "u"):
            locked.set()
            assert finish.wait(5)
            storage.copy(key, frozen)
            file = db.get(FileObject, "file")
            file.storage_key = frozen
            file.meta = {"processing": "queued"}
            db.commit()

    def collect():
        with factory() as db:
            purge_expired_presigns(db, storage, NOW)
            drain_object_deletions(db, storage)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(upload)
        assert locked.wait(5)
        second = pool.submit(collect)
        try:
            assert gc_waiting.wait(5)
            assert not second.done()
        finally:
            finish.set()
        first.result(timeout=10)
        second.result(timeout=10)
    with factory() as db:
        assert db.get(FileObject, "file").storage_key == frozen
        assert storage.get_bytes(frozen) == b"x"


def test_workspace_ttl_batches_and_idempotency(env):
    _, storage = env
    for i in range(5):
        put_old(storage, f"workspaces/old{i}.tgz")
    storage.put_bytes("workspaces/young.tgz", b"x")
    for _ in range(4):
        purge_expired_workspaces(storage, NOW)
    assert [i.key for i in storage.list_prefix("workspaces/")] == ["workspaces/young.tgz"]
    assert purge_expired_workspaces(storage, NOW) == 0


def test_lifecycle_is_idempotent_when_empty(env):
    factory, storage = env
    with factory() as db:
        assert not any(lifecycle_once(db, storage, NOW).values())
        assert not any(lifecycle_once(db, storage, NOW).values())


def test_capacity_warning_never_blocks_startup(monkeypatch):
    from ailab_platform.api import main
    from ailab_platform.db import init_db

    init_db()
    monkeypatch.setattr(settings, "provisioned_storage_bytes", 1)
    warnings = []
    monkeypatch.setattr(main.log, "warning", lambda message, *args: warnings.append(message))
    main.warn_storage_capacity()
    assert any("storage overcommitted" in message for message in warnings)
