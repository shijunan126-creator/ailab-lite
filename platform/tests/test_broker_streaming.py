"""Bulk Broker paths must never use a whole-file bytes interface."""

import hashlib
from pathlib import Path

import pytest

from ailab_platform.broker.service import ArtifactRejected, LeaseError
from ailab_platform.config import settings
from ailab_platform.storage import LocalStorage

from .test_broker_lease import _ApiStub, _manager_with


def _forbid(*args, **kwargs):
    raise AssertionError("whole-file buffering is forbidden")


def test_injection_downloads_with_run_binding_and_hard_limit(tmp_path, monkeypatch):
    api = _ApiStub({"f": {"status": "ready", "filename": "a.bin", "bytes": b"input"}}, ["f"])
    manager, provider, _ = _manager_with(api)
    calls = []

    def download(file_id, target, **context):
        calls.append((file_id, context))
        Path(target).write_bytes(b"input")

    monkeypatch.setattr(api, "download_file", download, raising=False)
    monkeypatch.setattr(api, "get_file_bytes", _forbid)
    monkeypatch.setattr(provider, "put_file", _forbid)
    lease = manager.create("run-stream")
    assert calls == [("f", {"run_id": "run-stream", "max_bytes": settings.max_upload_bytes})]
    assert provider.get_file(lease["sandbox_id"], "/workspace/inputs/a.bin") == b"input"


def test_delivery_streams_and_hashes_transferred_bytes(tmp_path, monkeypatch):
    manager, provider, _ = _manager_with(_ApiStub({}, []))
    manager.storage = storage = LocalStorage(tmp_path / "objects")
    lease = manager.create("run-stream")
    provider.put_file(lease["sandbox_id"], "/workspace/outputs/result.bin", b"result")
    monkeypatch.setattr(provider, "get_file", _forbid)
    monkeypatch.setattr(storage, "put_bytes", _forbid)
    result = manager.commit(lease["lease_id"], "/workspace/outputs/result.bin")
    assert result["size"] == 6
    assert result["sha256"] == hashlib.sha256(b"result").hexdigest()


def test_delivery_enforces_limit_even_if_stat_is_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "max_artifact_bytes", 5)
    manager, provider, _ = _manager_with(_ApiStub({}, []))
    manager.storage = LocalStorage(tmp_path / "objects")
    lease = manager.create("run-stream")
    provider.put_file(lease["sandbox_id"], "/workspace/outputs/growing.bin", b"123456")
    monkeypatch.setattr(provider, "file_size", lambda *args: 1)
    with pytest.raises(ArtifactRejected):
        manager.commit(lease["lease_id"], "/workspace/outputs/growing.bin")
    assert manager.platform.committed == []
    assert manager.storage.list_prefix("staging/").items == []


def test_snapshot_restore_uses_only_streams(tmp_path, monkeypatch):
    manager, provider, _ = _manager_with(_ApiStub({}, []))
    manager.storage = storage = LocalStorage(tmp_path / "objects")
    lease = manager.create("run-stream")
    provider.put_file(lease["sandbox_id"], "/workspace/outputs/result.bin", b"result")
    monkeypatch.setattr(provider, "get_file", _forbid)
    monkeypatch.setattr(provider, "put_file", _forbid)
    monkeypatch.setattr(storage, "get_bytes", _forbid)
    monkeypatch.setattr(storage, "put_bytes", _forbid)
    saved = manager.snapshot(lease["lease_id"])
    provider.exec(lease["sandbox_id"], "rm /workspace/outputs/result.bin", 5)
    manager._restore_workspace(lease, saved["key"])
    assert lease["restored"] is True
    assert provider.file_size(lease["sandbox_id"], "/workspace/outputs/result.bin") == 6


def test_failed_injection_releases_sandbox_and_spool(tmp_path, monkeypatch):
    api = _ApiStub({"f": {"status": "ready", "filename": "a.bin"}}, ["f"])
    manager, provider, _ = _manager_with(api)
    paths = []

    def broken(file_id, target, **context):
        paths.append(Path(target))
        Path(target).write_bytes(b"partial")
        raise TimeoutError("upstream stalled")

    monkeypatch.setattr(api, "download_file", broken, raising=False)
    with pytest.raises(LeaseError):
        manager.create("run-broken")
    assert paths and not paths[0].exists()
    assert manager.active_leases() == []
    assert len(provider.released) == 1


def test_startup_reclaims_only_its_own_crashed_spools(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    manager, _, _ = _manager_with(_ApiStub({}, []))
    stale = manager._transfer_root / "transfer-crashed"
    stale.mkdir(parents=True)
    (stale / "file").write_bytes(b"partial")
    other = manager._transfer_root.parent / "another-owner" / "file"
    other.parent.mkdir()
    other.write_bytes(b"keep")
    manager.create("run-restarted")
    assert not stale.exists() and other.read_bytes() == b"keep"


def test_storage_failure_removes_local_spool(tmp_path, monkeypatch):
    manager, provider, _ = _manager_with(_ApiStub({}, []))
    manager.storage = LocalStorage(tmp_path / "objects")
    lease = manager.create("run-storage-error")
    provider.put_file(lease["sandbox_id"], "/workspace/outputs/a.bin", b"result")

    def fail(*args, **kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(manager.storage, "put_stream", fail)
    with pytest.raises(OSError):
        manager.commit(lease["lease_id"], "/workspace/outputs/a.bin")
    assert not list(manager._transfer_root.iterdir())
    assert manager.platform.committed == []
