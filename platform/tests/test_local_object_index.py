"""Local object listing is indexed; legacy discovery is bounded and crash-recoverable."""

import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from ailab_platform.adapters import local_object_index
from ailab_platform.storage import LocalStorage


def all_keys(store, prefix="", limit=7):
    keys, cursor = [], None
    for _ in range(1000):
        page = store.list_prefix(prefix, cursor, limit)
        keys.extend(item.key for item in page)
        cursor = page.cursor
        if cursor is None:
            return keys
    raise AssertionError("bounded discovery failed to progress")


def test_completed_catalog_listing_never_scans_directories(tmp_path, monkeypatch):
    store = LocalStorage(tmp_path)
    for i in range(100):
        store.put_bytes(f"test/{i:04d}", b"data")
    assert len(all_keys(store)) == 100
    monkeypatch.setattr(local_object_index.os, "scandir", lambda *_: (_ for _ in ()).throw(AssertionError("unexpected full-prefix scan")))
    assert all_keys(store, "test/", 3) == [f"test/{i:04d}" for i in range(100)]
    with sqlite3.connect(store.index.path) as db:
        plan = str(
            db.execute(
                "EXPLAIN QUERY PLAN SELECT key FROM objects WHERE key>=? AND key<? AND key>? ORDER BY key LIMIT 4",
                ("test/", "test0", "test/0004"),
            ).fetchall()
        ).lower()
        assert "index" in plan and "search" in plan, plan
    store.close()


def test_legacy_discovery_resumes_after_instance_restart_and_retains_metadata(tmp_path, monkeypatch):
    for i in range(80):
        path = tmp_path / "legacy" / str(i)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"old")
    key = "legacy/0"
    stat = (tmp_path / key).stat()
    metadata = tmp_path / ".metadata"
    metadata.mkdir()
    (metadata / (hashlib.sha256(key.encode()).hexdigest() + ".json")).write_text(
        json.dumps({"size": 3, "mtime": stat.st_mtime_ns, "etag": hashlib.sha256(b"old").hexdigest(), "content_type": "text/plain"})
    )
    original = local_object_index.LocalObjectIndex.discover
    monkeypatch.setattr(local_object_index.LocalObjectIndex, "discover", lambda self: original(self, limit=5))
    first = LocalStorage(tmp_path)
    page = first.list_prefix("legacy/")
    assert page.items == [] and page.cursor
    first.close()
    resumed = LocalStorage(tmp_path)
    assert all_keys(resumed, "legacy/") == sorted(f"legacy/{i}" for i in range(80))
    assert resumed.info(key).content_type == "text/plain"
    assert resumed.info("legacy/1").etag == hashlib.sha256(b"old").hexdigest()
    resumed.close()


def test_legacy_discovery_has_an_entry_budget_and_closes_handle(tmp_path):
    for i in range(500):
        (tmp_path / str(i)).write_bytes(b"x")
    store = LocalStorage(tmp_path)
    assert not store.index.discover(limit=9)
    with sqlite3.connect(store.index.path) as db:
        assert db.execute("SELECT count(*) FROM objects").fetchone()[0] <= 9
    handle = store.index.entries
    assert handle is not None
    store.close()
    assert store.index.entries is None
    # The completed iterator must not yield an unbounded suffix after close.
    assert list(handle) == []


def test_catalog_omits_symlinks_and_private_metadata(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "private").write_bytes(b"private")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    store = LocalStorage(tmp_path)
    store.put_bytes("uploads/user/file/.env", b"user content")
    assert all_keys(store) == ["uploads/user/file/.env"]
    with pytest.raises(ValueError, match="escapes root"):
        store.info("link/private")
    store.close()


def test_new_writes_survive_reopen_and_pending_failure_does_not_publish_object(tmp_path):
    store = LocalStorage(tmp_path)
    store.put_bytes("new/complete", b"one", "text/plain")

    def broken():
        yield b"partial"
        raise OSError("cancelled")

    with pytest.raises(OSError):
        store.put_stream("new/failed", broken(), max_bytes=100)
    store.close()
    reopened = LocalStorage(tmp_path)
    assert all_keys(reopened) == ["new/complete"]
    assert reopened.info("new/complete").etag == hashlib.sha256(b"one").hexdigest()
    assert not list((tmp_path / ".metadata").glob("*.tmp"))
    reopened.delete("new/complete")
    assert all_keys(reopened) == []
    reopened.close()


def test_independent_storage_instances_publish_concurrently(tmp_path):
    def write(i):
        store = LocalStorage(tmp_path)
        try:
            store.put_bytes(f"objects/{i:03d}", str(i).encode())
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(30)))
    store = LocalStorage(tmp_path)
    assert len(all_keys(store)) == 30
    store.close()


def test_transfer_metadata_survives_crash_between_rename_and_catalog_update(tmp_path, monkeypatch):
    store = LocalStorage(tmp_path)
    replace = os.replace

    def interrupted(*args):
        replace(*args)
        raise OSError("crash after rename")

    monkeypatch.setattr(local_object_index.os, "replace", interrupted)
    with pytest.raises(OSError):
        store.put_bytes("object", b"committed bytes", "text/plain")
    store.close()
    reopened = LocalStorage(tmp_path)
    assert all_keys(reopened) == ["object"]
    assert reopened.info("object").etag == hashlib.sha256(b"committed bytes").hexdigest()
    reopened.close()


def test_delete_and_replacement_cannot_leave_an_unlisted_live_object(tmp_path, monkeypatch):
    from pathlib import Path
    from threading import Event

    store, other = LocalStorage(tmp_path), LocalStorage(tmp_path)
    store.put_bytes("same", b"old")
    assert all_keys(store) == ["same"]  # completed discovery cannot repair a lost row
    unlinked, resume, completed = Event(), Event(), Event()
    unlink = Path.unlink

    def paused(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        if path == tmp_path / "same":
            unlinked.set()
            assert resume.wait(5)

    def replacement():
        other.put_bytes("same", b"new")
        completed.set()

    monkeypatch.setattr(Path, "unlink", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        deletion = pool.submit(store.delete, "same")
        assert unlinked.wait(5)
        writing = pool.submit(replacement)
        try:
            assert not completed.wait(0.2)
        finally:
            resume.set()
        deletion.result(timeout=5)
        writing.result(timeout=5)
    assert store.get_bytes("same") == b"new"
    assert all_keys(store) == ["same"]
    store.close()
    other.close()


def test_fifty_open_close_cycles_release_discovery_fds(tmp_path):
    (tmp_path / "legacy").mkdir()
    for i in range(100):
        (tmp_path / "legacy" / str(i)).write_bytes(b"x")
    before = len(os.listdir("/dev/fd")) if os.path.isdir("/dev/fd") else None
    for _ in range(50):
        store = LocalStorage(tmp_path)
        store.index.discover(limit=1)
        store.close()
    after = len(os.listdir("/dev/fd")) if before is not None else None
    if before is not None:
        assert after <= before + 1


def test_normal_write_failure_clears_registration_and_crash_tmp_uses_lease(tmp_path):
    import time

    store = LocalStorage(tmp_path)
    with pytest.raises(ValueError):
        store.put_stream("failed", iter([b"too big"]), max_bytes=1)
    with sqlite3.connect(store.index.path) as db:
        assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM objects WHERE key='failed'").fetchone()[0] == 0
    token = store.index.register("crashed")
    temporary = tmp_path / ".metadata" / (token + ".tmp")
    temporary.write_bytes(b"partial")
    assert store.maintenance(now=time.time() + 100) == 0
    assert temporary.exists()
    assert store.maintenance(now=time.time() + 1100) == 1
    assert not temporary.exists()
    with sqlite3.connect(store.index.path) as db:
        assert db.execute("SELECT count(*) FROM pending").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM objects WHERE key='crashed'").fetchone()[0] == 0
    store.close()


def test_cancelled_writer_cannot_remove_other_writer_registration(tmp_path):
    store = LocalStorage(tmp_path)
    first = store.index.register("same")
    second = store.index.register("same")
    store.index.end_write("same", first)
    with sqlite3.connect(store.index.path) as db:
        assert db.execute("SELECT count(*) FROM objects WHERE key='same'").fetchone()[0] == 1
    (tmp_path / "same").write_bytes(b"published before crash")
    store.index.end_write("same", second)
    assert all_keys(store) == ["same"]
    store.close()


def test_late_metadata_after_deletion_cannot_recreate_catalog_row(tmp_path):
    store = LocalStorage(tmp_path)
    store.put_bytes("deleted", b"old")
    stat = (tmp_path / "deleted").stat()
    store.delete("deleted")
    store.index.record("deleted", 3, stat.st_mtime_ns, "stale", "text/plain")
    assert store.index.get("deleted") == {}
    store.close()


def test_pagination_helper_does_not_treat_empty_migration_page_as_complete():
    from ailab_platform.ports.storage import ObjectPage, StoredObject
    from ailab_platform.storage import iter_objects

    class Migrating:
        def list_prefix(self, prefix, cursor=None, limit=100):
            if cursor is None:
                return ObjectPage([], "in-progress")
            return ObjectPage([StoredObject("a", 1, datetime.now(UTC))])

    assert [item.key for item in iter_objects(Migrating(), "")] == ["a"]


def test_queued_legacy_directory_changed_to_symlink_is_not_followed(tmp_path):
    old = tmp_path / "queued"
    old.mkdir()
    (old / "file").write_bytes(b"old")
    store = LocalStorage(tmp_path)
    store.index.discover(limit=1)
    store.close()
    moved = tmp_path.parent / (tmp_path.name + "-moved")
    old.rename(moved)
    old.symlink_to(moved, target_is_directory=True)
    restarted = LocalStorage(tmp_path)
    assert all_keys(restarted) == []
    restarted.close()
