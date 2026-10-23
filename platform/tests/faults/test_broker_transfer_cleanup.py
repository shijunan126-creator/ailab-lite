"""Cancelled preparation must free both the spool and its unactivated sandbox."""

import asyncio
from pathlib import Path

import pytest

from tests.test_broker_lease import _ApiStub, _manager_with


def test_cancelled_download_releases_preparing_lease(monkeypatch):
    api = _ApiStub({"f": {"status": "ready", "filename": "a.bin"}}, ["f"])
    manager, provider, _ = _manager_with(api)
    paths = []

    def cancelled(file_id, target, **kwargs):
        paths.append(Path(target))
        Path(target).write_bytes(b"partial")
        raise asyncio.CancelledError

    monkeypatch.setattr(api, "download_file", cancelled)
    with pytest.raises(asyncio.CancelledError):
        manager.create("run-cancelled")
    assert paths and not paths[0].exists()
    assert manager.active_leases() == []
    assert len(provider.released) == 1
