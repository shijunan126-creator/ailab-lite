"""Provider absence must be confirmed, never guessed from a network failure."""

import json
import subprocess
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ailab_platform.sandbox import docker
from ailab_platform.sandbox.base import SandboxError
from ailab_platform.sandbox.opensandbox import OpenSandboxProvider


def test_docker_nonzero_delete_is_retryable_not_success(monkeypatch):
    provider = docker.DockerSandbox()
    monkeypatch.setattr(docker, "_run", lambda *_a, **_k: subprocess.CompletedProcess([], 1, b"", b"permission denied"))
    with pytest.raises(SandboxError, match="delete failed"):
        provider.release("container")
    with pytest.raises(SandboxError, match="inspect failed"):
        provider.inspect("container")
    monkeypatch.setattr(docker, "_run", lambda *_a, **_k: subprocess.CompletedProcess([], 1, b"", b"No such container: id"))
    provider.release("container")
    assert provider.inspect("container") is None


def test_docker_root_only_metadata_overrides_creation_labels(monkeypatch):
    creation = {"Id": "immutable-ref", "State": {"Running": True}, "Config": {"Labels": {"ailab.state": "creating"}}}

    def run(args, **_):
        payload = [creation] if args[1] == "inspect" else {"ailab.state": "active", "ailab.expires_at": "renewed"}
        return subprocess.CompletedProcess(args, 0, json.dumps(payload).encode(), b"")

    monkeypatch.setattr(docker, "_run", run)
    resource = docker.DockerSandbox().inspect("immutable-ref")
    assert resource.labels["ailab.state"] == "active"
    assert resource.labels["ailab.expires_at"] == "renewed"


@pytest.mark.parametrize("stopped", [None, {"Id": "ref", "State": {"Running": False}, "Config": {"Labels": {}}}])
def test_docker_inspect_rechecks_resource_if_it_exits_during_metadata_read(monkeypatch, stopped):
    active = {"Id": "ref", "State": {"Running": True}, "Config": {"Labels": {}}}
    replies = iter(
        [
            subprocess.CompletedProcess([], 0, json.dumps([active]).encode(), b""),
            subprocess.CompletedProcess([], 1, b"", b"container is not running"),
            subprocess.CompletedProcess([], 0, json.dumps([stopped]).encode(), b"")
            if stopped
            else subprocess.CompletedProcess([], 1, b"", b"No such object: ref"),
        ]
    )
    monkeypatch.setattr(docker, "_run", lambda *_a, **_k: next(replies))
    resource = docker.DockerSandbox().inspect("ref")
    assert resource is None if stopped is None else resource.running is False


def test_docker_inspect_does_not_hide_metadata_failure_on_live_resource(monkeypatch):
    active = {"Id": "ref", "State": {"Running": True}, "Config": {"Labels": {}}}
    monkeypatch.setattr(
        docker,
        "_run",
        lambda args, **_: subprocess.CompletedProcess(
            args, 0 if args[1] == "inspect" else 1, json.dumps([active]).encode(), b"permission denied"
        ),
    )
    with pytest.raises(SandboxError, match="metadata unavailable"):
        docker.DockerSandbox().inspect("ref")


def test_docker_release_waits_for_concurrent_auto_removal(monkeypatch):
    stopped = {"Id": "ref", "State": {"Running": False}, "Config": {"Labels": {}}}
    replies = iter(
        [
            subprocess.CompletedProcess([], 1, b"", b"removal of container ref is already in progress"),
            subprocess.CompletedProcess([], 0, json.dumps([stopped]).encode(), b""),
            subprocess.CompletedProcess([], 1, b"", b"No such object: ref"),
        ]
    )
    monkeypatch.setattr(docker, "_run", lambda *_a, **_k: next(replies))
    monkeypatch.setattr(docker.time, "sleep", lambda _: None)
    docker.DockerSandbox().release("ref")


def test_opensandbox_connection_failure_does_not_erase_resource():
    provider = OpenSandboxProvider.__new__(OpenSandboxProvider)

    def unavailable(*_):
        raise ConnectionError("server unavailable")

    provider._manager = SimpleNamespace(get_sandbox_info=unavailable, kill_sandbox=unavailable)
    with pytest.raises(SandboxError, match="inspect failed"):
        provider.inspect("reference")
    with pytest.raises(SandboxError, match="delete failed"):
        provider.release("reference")


def test_opensandbox_renew_passes_absolute_deadline_with_backstop():
    provider = OpenSandboxProvider.__new__(OpenSandboxProvider)
    calls = []
    provider._manager = SimpleNamespace(renew_sandbox=lambda ref, timeout: calls.append((ref, timeout.total_seconds())))
    provider.renew("reference", datetime.now(UTC))
    assert calls[0][0] == "reference"
    assert 299 < calls[0][1] <= 300


def test_opensandbox_interrupts_tracked_executions():
    import threading

    provider = OpenSandboxProvider.__new__(OpenSandboxProvider)
    provider._lock = threading.Lock()
    provider._executions = {"reference": {"exec-1", "exec-2"}}
    interrupted = []
    provider._sandbox = lambda _: SimpleNamespace(commands=SimpleNamespace(interrupt=interrupted.append))
    assert provider.cancel_exec("reference") is True
    assert set(interrupted) == {"exec-1", "exec-2"}


def test_opensandbox_confirmed_loss_closes_cached_client():
    import threading

    class Missing(Exception):
        status_code = 404

    def missing(_):
        raise Missing()

    closed = []
    provider = OpenSandboxProvider.__new__(OpenSandboxProvider)
    provider._lock = threading.Lock()
    provider._cache = {"reference": SimpleNamespace(close=lambda: closed.append(True))}
    provider._manager = SimpleNamespace(get_sandbox_info=missing)
    assert provider.inspect("reference") is None
    assert closed == [True] and provider._cache == {}
