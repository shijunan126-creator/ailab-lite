"""TTL invalidates availability before physical deletion; failed deletes stay charged."""

# ruff: noqa: F811

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ailab_platform.api import derived_assets as assets
from ailab_platform.api.preview_requests import request_preview
from ailab_platform.models import DerivedAsset, DocumentLane, FileObject, RenderedPage
from tests.test_derived_assets import initialize, ledger, totals, write  # noqa: F401


def test_expired_page_is_removed_from_rows_and_metadata_before_rebuild(ledger):
    factory, storage = ledger
    initialize(factory, storage)
    with factory.begin() as db:
        db.add_all([DocumentLane(name="render"), DocumentLane(name="ocr")])
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


def test_capacity_pressure_evicts_the_oldest_rebuildable_caches_and_nothing_in_use(ledger, monkeypatch):
    from ailab_platform.config import settings

    factory, storage = ledger
    initialize(factory, storage)
    monkeypatch.setattr(settings, "derived_budget_bytes", 2 * 1024**2)
    monkeypatch.setattr(settings, "derived_evict_high_ratio", 0.9)
    monkeypatch.setattr(settings, "derived_evict_low_ratio", 0.5)
    now = datetime.now(UTC)
    chunk = b"x" * (180 * 1024)  # with the young one, eleven pass the high mark; six evictions reach the low mark
    keys = [f"derived/f/preview/{i}/p-1.png" for i in range(10)]
    for i, key in enumerate(keys):
        write(factory, storage, key, chunk, now=now - timedelta(hours=10 - i))
    protected = {
        "pinned": keys[0],
        "native": "derived/f/office/g/text.txt",
        "ocr": "derived/f/ocr/t.txt",
        "young": "derived/f/preview/new/p-1.png",
    }
    with factory.begin() as db:
        file = db.get(FileObject, "f")
        file.meta = {
            **file.meta,
            "preview": {"status": "ready", "kind": "pages", "pages": ["p-1.png"]},
            "preview_assets": {"p-1.png": {"key": keys[1]}},
        }
        db.add(RenderedPage(file_id="f", page=1, version="poppler-1280-v1", key=keys[1], digest="d"))
    with factory.begin() as db:
        assets.pin(db, keys[0], "in-use", kind="view", run_id="r", expires_at=now + timedelta(hours=1))
    write(factory, storage, protected["native"], b"n", now=now - timedelta(days=2), kind="native")
    write(factory, storage, protected["ocr"], b"o", now=now - timedelta(days=2), kind="ocr", reconstructible=False)
    write(factory, storage, protected["young"], chunk, now=now)
    with factory() as db:
        assert assets.evict_oldest(db, storage, now=now) == 6
    survivors = {key for key in [*keys, *protected.values()] if storage.exists(key)}
    # The pinned oldest one stays; the next six oldest unpinned caches go. The young cache is mid-turn.
    assert survivors == {keys[0], *keys[7:], protected["native"], protected["ocr"], protected["young"]}
    with factory() as db:
        # Evicting a page also removes its availability, so the preview is rebuilt rather than served broken.
        assert db.scalar(select(RenderedPage.file_id)) is None
        assert "preview" not in db.get(FileObject, "f").meta
        assert assets.evict_oldest(db, storage, now=now) == 0  # back under the low mark: nothing more to do
    used, reserved = totals(factory)
    assert reserved == 0 and used <= 2 * 1024**2 * 0.9


def test_eviction_is_idle_below_the_high_mark_and_keeps_charge_when_delete_fails(ledger, monkeypatch):
    from ailab_platform.config import settings

    factory, storage = ledger
    initialize(factory, storage)
    monkeypatch.setattr(settings, "derived_budget_bytes", 1024**2)
    monkeypatch.setattr(settings, "derived_evict_high_ratio", 0.5)
    monkeypatch.setattr(settings, "derived_evict_low_ratio", 0.25)
    now = datetime.now(UTC)
    write(factory, storage, "derived/f/preview/a/p-1.png", b"x" * 400 * 1024, now=now - timedelta(hours=2))
    with factory() as db:
        assert assets.evict_oldest(db, storage, now=now) == 0  # 40% is under the 50% high mark
    write(factory, storage, "derived/f/preview/b/p-1.png", b"y" * 200 * 1024, now=now - timedelta(hours=1))
    original = storage.delete

    def broken(key, *args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(storage, "delete", broken)
    before = totals(factory)
    with factory() as db:
        assert assets.evict_oldest(db, storage, now=now) == 0
    assert totals(factory) == before  # bytes stay charged until they are really gone
    monkeypatch.setattr(storage, "delete", original)
    with factory() as db:
        assert assets.collect_batch(db, storage, now=now + timedelta(seconds=settings.derived_write_lease_seconds + 1)) >= 1
