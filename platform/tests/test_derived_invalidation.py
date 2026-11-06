"""TTL invalidates availability before physical deletion; failed deletes stay charged."""

# ruff: noqa: F811

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ailab_platform.api import derived_assets as assets
from ailab_platform.api.preview_requests import request_preview
from ailab_platform.models import DerivedAsset, FileObject, RenderedPage
from tests.test_derived_assets import initialize, ledger, totals, write  # noqa: F401


def test_expired_page_is_removed_from_rows_and_metadata_before_rebuild(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    old = datetime.now(UTC) - timedelta(days=31)
    key = "derived/" + "f/preview/old/p-1.png"
    write(factory, storage, key, b"page", now=old)
    with factory.begin() as db:
        file = db.get(FileObject, "f")
        file.status = "ready"
        file.meta = {
            **file.meta,
            "preview": {"status": "ready", "kind": "pages", "pages": ["p-1.png"]},
            "preview_assets": {"p-1.png": {"key": key}},
        }
        db.add(RenderedPage(file_id="f", page=1, version="poppler-1280-v1", key=key, digest="digest"))
    with factory() as db:
        assert assets.collect_batch(db, storage) == 1
        file = db.get(FileObject, "f", populate_existing=True)
        assert "preview" not in file.meta
        assert db.scalar(select(RenderedPage.file_id)) is None
        manifest, job = request_preview(db, file)
        assert manifest["status"] == "pending" and job
        db.commit()
    assert not storage.exists(key) and totals(factory) == (0, 0)


def test_storage_failure_keeps_capacity_but_never_advertises_expired_page_as_ready(ledger, monkeypatch):
    factory, storage = ledger
    initialize(factory, storage)
    key = "derived/" + "f/preview/old/p-1.png"
    write(factory, storage, key, b"expired", now=datetime.now(UTC) - timedelta(days=31))
    with factory.begin() as db:
        db.add(RenderedPage(file_id="f", page=1, version="poppler-1280-v1", key=key, digest="digest"))
    delete = storage.delete
    monkeypatch.setattr(storage, "delete", lambda key: (_ for _ in ()).throw(OSError("storage unavailable")))
    with factory() as db:
        assert assets.collect_batch(db, storage) == 0
        assert db.scalar(select(RenderedPage.file_id)) is None
        row = db.get(DerivedAsset, key)
        assert row.state == "deleting"
        due = assets.aware(row.lease_until)
    assert totals(factory) == (len(b"expired"), 0)
    monkeypatch.setattr(storage, "delete", delete)
    with factory() as db:
        assert assets.collect_batch(db, storage, now=due + timedelta(seconds=1)) == 1
    assert totals(factory) == (0, 0)
