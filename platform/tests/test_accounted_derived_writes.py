"""Write ordering and ambiguous publication compensation for the common service adapter."""

import pytest

from ailab_platform.derived_writes import AccountedWrites
from ailab_platform.storage import LocalStorage


class Authority:
    def __init__(self, storage):
        self.storage = storage
        self.rows = {}
        self.events = []

    def reserve_derived(self, **body):
        key = body["key"]
        self.events.append(("reserved", key))
        assert key not in self.rows
        self.rows[key] = {"state": "writing", "token": "grant-" + str(len(self.rows)), "maximum": body["max_bytes"]}
        return {"key": key, "token": self.rows[key]["token"]}

    def finish_derived(self, key, token):
        assert self.rows[key]["token"] == token
        assert self.storage.info(key).size <= self.rows[key]["maximum"]
        self.rows[key]["state"] = "staged"
        self.events.append(("staged", key))

    def abandon_derived_writer(self, job_id):
        result = []
        for key, row in self.rows.items():
            if row["state"] in {"writing", "staged"}:
                row["state"] = "deleting"
            if row["state"] == "deleting":
                result.append({"key": key, "token": row["token"]})
        return {"deletable": result[:32]}

    def discard_derived_batch(self, items):
        for receipt in items:
            assert not self.storage.exists(receipt["key"])
            self.rows.pop(receipt["key"])


def test_every_storage_write_reserves_before_io_and_cleanup_never_deletes_published_bytes(tmp_path):
    storage = LocalStorage(tmp_path)
    authority = Authority(storage)
    writer = AccountedWrites(storage, authority, "source", "job")
    original_put = storage.put_bytes

    def checked_put(key, data, content_type):
        assert authority.rows[key]["state"] == "writing"
        return original_put(key, data, content_type)

    storage.put_bytes = checked_put
    staged = "derived/file/preview/staged.txt"
    published = "derived/file/preview/published.txt"
    writer.put_bytes(staged, b"partial")
    writer.put_bytes(published, b"canonical")
    authority.rows[published]["state"] = "ready"  # commit won, response was lost
    writer.cleanup()
    assert not storage.exists(staged)
    assert storage.get_bytes(published) == b"canonical"
    assert set(authority.rows) == {published}
    assert not hasattr(writer, "receipts")  # inventory is persistent, never an unbounded request list
    storage.close()


def test_broker_handoff_retains_staged_objects_until_worker_publication_or_compensation(tmp_path):
    storage = LocalStorage(tmp_path)
    authority = Authority(storage)
    broker = AccountedWrites(storage, authority, "source", "job")
    key = "derived/file/preview/page.png"
    broker.put_bytes(key, b"image")
    broker.handoff()
    broker.cleanup()
    assert storage.exists(key) and authority.rows[key]["state"] == "staged"
    worker = AccountedWrites(storage, authority, "source", "job")
    worker.cleanup()  # same durable writer identity; failed metadata binding
    assert not storage.exists(key) and authority.rows == {}
    storage.close()


def test_failed_put_keeps_capacity_liability_until_physical_delete_succeeds(tmp_path, monkeypatch):
    storage = LocalStorage(tmp_path)
    authority = Authority(storage)
    writer = AccountedWrites(storage, authority, "source", "job")
    key = "derived/file/preview/partial"

    def failed_put(*args):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "put_bytes", failed_put)
    with pytest.raises(OSError, match="disk full"):
        writer.put_bytes(key, b"bytes")
    monkeypatch.setattr(storage, "delete", lambda key: (_ for _ in ()).throw(OSError("storage down")))
    with pytest.raises(OSError, match="storage down"):
        writer.cleanup()
    assert authority.rows[key]["state"] == "deleting"
    storage.close()


def test_api_ocr_pages_and_aggregate_publish_once_with_exact_capacity():
    from sqlalchemy import select

    from ailab_platform.api.document_sources import ocr_records
    from ailab_platform.api.legacy_ocr_assets import store_page
    from ailab_platform.api.routes_ocr import publish_extraction
    from ailab_platform.db import SessionLocal, init_db
    from ailab_platform.models import DerivedAsset, DerivedBudget
    from ailab_platform.storage import get_storage
    from tests.test_native_document_scopes import make_file

    init_db()
    with SessionLocal() as db:
        file, _ = make_file(db)
        file.meta = {**file.meta, "ocr_generation": "owned-ocr", "ocr": "running"}
        db.get(DerivedBudget, 1).backfill_complete = True
        db.commit()
        key = store_page(
            db, file, "owned-ocr", 0, {"text": "文字与图形覆盖独立", "engine": "rapidocr:test", "backend": "cpu"}, attempt=None
        )
        records = ocr_records(db, file, [0])
        assert records["0"]["text_key"] == key
        count = publish_extraction(db, file, [1], records)
        aggregate = file.meta["ocr_text_key"]
        assert get_storage().get_bytes(aggregate).decode() == "\f[page:1]\n文字与图形覆盖独立"
        assert count == len("\f[page:1]\n文字与图形覆盖独立")
        again = publish_extraction(db, file, [1], records)
        assert again == count and file.meta["ocr_text_key"] == aggregate
        rows = list(db.scalars(select(DerivedAsset).where(DerivedAsset.file_id == file.id)))
        assert len(rows) == 2 and all(row.state == "ready" and row.reserved_bytes == 0 for row in rows)
        assert sum(row.size_bytes for row in rows) == sum(get_storage().size(row.key) for row in rows)


def test_api_owned_write_rejects_late_generation_and_cleans_only_its_unpublished_bytes(monkeypatch):
    from fastapi import HTTPException
    from sqlalchemy import select

    from ailab_platform.api.legacy_ocr_assets import store_page
    from ailab_platform.db import SessionLocal, init_db
    from ailab_platform.models import DerivedAsset, DerivedBudget, FileObject, OcrPageResult
    from ailab_platform.storage import get_storage
    from tests.test_native_document_scopes import make_file

    init_db()
    with SessionLocal() as db:
        file, _ = make_file(db)
        file.meta = {**file.meta, "ocr_generation": "old", "ocr": "running"}
        db.get(DerivedBudget, 1).backfill_complete = True
        db.commit()
        storage = get_storage()
        original = storage.put_bytes

        def race(key, body, content_type):
            original(key, body, content_type)
            with SessionLocal() as other:
                current = other.get(FileObject, file.id)
                current.meta = {**current.meta, "ocr_generation": "new"}
                other.commit()

        monkeypatch.setattr(storage, "put_bytes", race)
        with pytest.raises(HTTPException) as caught:
            store_page(db, file, "old", 0, {"text": "late", "engine": "rapidocr:test", "backend": "cpu"}, attempt=None)
        assert caught.value.status_code == 409
        assert db.scalar(select(DerivedAsset.key).where(DerivedAsset.file_id == file.id)) is None
        assert db.scalar(select(OcrPageResult.file_id).where(OcrPageResult.file_id == file.id)) is None
        assert not storage.exists(f"derived/{file.id}/ocr/old/0.txt")
