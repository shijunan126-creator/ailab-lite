"""Uploads must create parents as the exec user before execd receives bytes."""

from types import SimpleNamespace

import pytest

from ailab_platform.sandbox.base import ExecResult, SandboxError
from ailab_platform.sandbox.opensandbox import OpenSandboxProvider


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("writable", [False, True])
def test_upload_creates_parent_before_sdk_write(tmp_path, streaming, writable):
    provider = OpenSandboxProvider.__new__(OpenSandboxProvider)
    commands, writes = [], []

    def execute(ref, command, timeout):
        commands.append(command)
        if len(commands) == 1:
            # Model execd creating a missing root-owned parent if preflight omits mkdir.
            assert 'mkdir -p -- "$parent"' in command
            assert command.index('case "$rp"') < command.index("mkdir -p")
            assert '[ ! -L "$p" ] &&' in command
            return ExecResult(0 if writable else 1, "", "", 0)
        return ExecResult(0, "", "", 0)

    provider.exec = execute
    provider._sandbox = lambda ref: SimpleNamespace(files=SimpleNamespace(write_file=lambda *a, **k: writes.append((a, k))))
    source = tmp_path / "source"
    source.write_bytes(b"hello")

    def upload():
        if streaming:
            provider.put_file_from_path("ref", "/workspace/new/deep/file", source, max_bytes=5)
        else:
            provider.put_file("ref", "/workspace/new/deep/file", b"hello")

    if writable:
        upload()
        assert len(writes) == 1
    else:
        with pytest.raises(SandboxError):
            upload()
        assert writes == []


@pytest.mark.parametrize("mode,policy", [("sidecar", "deny"), ("kubernetes", None)])
def test_network_policy_mode_is_explicit_and_creation_never_retries(monkeypatch, mode, policy):
    pytest.importorskip("opensandbox")
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "osb_network_mode", mode)
    calls = []

    def create(*args, **kwargs):
        calls.append(kwargs)
        raise ValueError("unsupported runtime")

    provider = OpenSandboxProvider.__new__(OpenSandboxProvider)
    provider._SandboxSync = SimpleNamespace(create=create)
    provider._config = object()
    with pytest.raises(SandboxError, match="create failed"):
        provider.acquire("lease", "image")
    assert len(calls) == 1
    actual = calls[0]["network_policy"]
    assert (actual.default_action if actual else None) == policy


def test_secure_kubernetes_mode_requires_isolation_acceptance(monkeypatch):
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "profile", "secure")
    monkeypatch.setattr(settings, "sandbox_provider", "opensandbox")
    monkeypatch.setattr(settings, "osb_network_mode", "kubernetes")
    monkeypatch.setattr(settings, "osb_kubernetes_isolation_verified", False)
    with pytest.raises(RuntimeError, match="Kubernetes sandbox isolation must be verified"):
        settings.validate_secrets()
