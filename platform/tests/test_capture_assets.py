"""Canonical screenshot ownership, transactional publication and physical cleanup."""

# ruff: noqa: F811

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from ailab_platform.api import derived_assets as assets
from ailab_platform.api import viewer_captures as captures
from ailab_platform.api.reaper import drain_object_deletions
from ailab_platform.models import DerivedAsset, DerivedAssetRef, FileObject, Run, ViewerCapture
from tests.test_derived_assets import initialize, ledger, totals  # noqa: F401


def setup_capture(factory, storage, monkeypatch):
    initialize(factory, storage)
    monkeypatch.setattr(captures, "get_storage", lambda: storage)
    monkeypatch.setattr("ailab_platform.api.owned_assets.get_storage", lambda: storage)
    with factory.begin() as db:
        file = db.get(FileObject, "f")
        file.status = "ready"
        run = db.get(Run, "r")
        run.status, run.options = "running", {"document_tools_version": 2, "mode": "chat"}
    return {"file_id": "f", "version": "source", "source": "browser", "at": "page:1"}


def test_canonical_capture_survives_temporary_ttl_but_last_message_release_cleans_capacity(ledger, monkeypatch):
    factory, storage = ledger
    target = setup_capture(factory, storage, monkeypatch)
    data = b"normalized-image"
    with factory() as db:
        result = captures.save(db, db.get(Run, "r"), "one-operation", target, data)
        again = captures.save(db, db.get(Run, "r"), "one-operation", target, data)
        assert again == result
        row = db.get(ViewerCapture, result["capture_id"])
        key = row.storage_key
        assert row.digest == hashlib.sha256(data).hexdigest()
        assert captures.purge(db, storage, datetime.now(UTC) + timedelta(days=31)) == 0
        assert db.scalar(select(DerivedAssetRef.id).where(DerivedAssetRef.key == key))
        assert not db.get(DerivedAsset, key).reconstructible
    assert totals(factory) == (len(data), 0)
    with factory() as db:
        captures.release_runs(db, ["r"])
        db.commit()
        assets.collect_batch(db, storage, transient_only=True)
    assert not storage.exists(key) and totals(factory) == (0, 0)


def test_claim_revoked_during_write_cannot_bind_or_leave_an_uncharged_object(ledger, monkeypatch):
    factory, storage = ledger
    target = setup_capture(factory, storage, monkeypatch)
    original = storage.put_bytes

    def canceled(key, data, content_type):
        original(key, data, content_type)
        with factory.begin() as other:
            other.get(Run, "r").status = "canceled"

    monkeypatch.setattr(storage, "put_bytes", canceled)
    with factory() as db:
        with pytest.raises(HTTPException) as error:
            captures.save(db, db.get(Run, "r"), "late", target, b"pixels")
        assert error.value.status_code == 409
        assert db.scalar(select(ViewerCapture.id)) is None
        assert db.scalar(select(DerivedAsset.key)) is None
    assert totals(factory) == (0, 0)


def test_failed_legacy_expiry_delete_retains_durable_intent_and_capacity(ledger, monkeypatch):
    factory, storage = ledger
    target = setup_capture(factory, storage, monkeypatch)
    with factory() as db:
        run = db.get(Run, "r")
        run.options = {}
        db.commit()
        result = captures.save(db, run, "legacy", target, b"legacy-pixels")
        key = db.get(ViewerCapture, result["capture_id"]).storage_key
        assert captures.purge(db, storage, datetime.now(UTC) + timedelta(minutes=11)) == 1
        delete = storage.delete
        monkeypatch.setattr(storage, "delete", lambda key: (_ for _ in ()).throw(OSError("storage unavailable")))
        assert drain_object_deletions(db, storage) == 0
        assert db.get(DerivedAsset, key).state == "deleting"
        assert totals(factory) == (len(b"legacy-pixels"), 0)
        monkeypatch.setattr(storage, "delete", delete)
        assert drain_object_deletions(db, storage) == 1
    assert not storage.exists(key) and totals(factory) == (0, 0)
