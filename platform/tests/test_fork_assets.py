"""Fork reservations survive crashes and publish atomically with the new run."""

# ruff: noqa: F811

import pytest

from ailab_platform.api import derived_assets as assets
from ailab_platform.api.fork_assets import PreviewCopies
from ailab_platform.models import DerivedAsset, Run
from tests.test_derived_assets import initialize, ledger, totals  # noqa: F401


def test_fork_preview_reserves_before_io_and_binds_only_on_branch_commit(ledger, monkeypatch):
    factory, storage = ledger
    initialize(factory, storage)
    storage.put_bytes("previews/r/1/p-01.jpg", b"preview")
    copies = PreviewCopies(storage, lambda: None)
    original = storage.copy

    def observed(source, destination):
        assert totals(factory) == (0, len(b"preview"))
        original(source, destination)

    monkeypatch.setattr(storage, "copy", observed)
    with factory() as db:
        copies.prepare(db, [db.get(Run, "r")], {"r": "branch"}, set())
        assert db.get(Run, "branch") is None
        run = Run(id="branch", user_id="u", prompt="fork", model="test")
        db.add(run)
        db.flush()
        copies.publish(db, run)
        db.commit()
        assert db.get(DerivedAsset, "previews/branch/1/p-01.jpg").run_id == "branch"
    assert totals(factory) == (len(b"preview"), 0)


def test_fork_failed_copy_cleanup_keeps_capacity_until_physical_delete(ledger, monkeypatch):
    factory, storage = ledger
    initialize(factory, storage)
    storage.put_bytes("previews/r/1/p-01.jpg", b"preview")
    copies = PreviewCopies(storage, lambda: None)

    def failed(source, destination):
        storage.put_bytes(destination, b"partial")
        raise OSError("copy interrupted")

    monkeypatch.setattr(storage, "copy", failed)
    with factory() as db:
        with pytest.raises(OSError, match="interrupted"):
            copies.prepare(db, [db.get(Run, "r")], {"r": "branch"}, set())
        delete = storage.delete
        monkeypatch.setattr(storage, "delete", lambda key: (_ for _ in ()).throw(OSError("offline")))
        copies.cleanup(db)
        assert totals(factory) == (0, len(b"preview"))
        assert db.get(DerivedAsset, "previews/branch/1/p-01.jpg").state == "deleting"
        monkeypatch.setattr(storage, "delete", delete)
        assets.collect_batch(db, storage)
    assert not storage.exists("previews/branch/1/p-01.jpg") and totals(factory) == (0, 0)
