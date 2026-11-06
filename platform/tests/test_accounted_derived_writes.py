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
