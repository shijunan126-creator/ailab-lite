"""Path safety at the provider boundary.

The Broker normalizes paths lexically; a sandbox can still create symlinks.
Providers must refuse to read/write through them before the kernel follows the
link. Docker/OpenSandbox cover this with ``realpath`` + ``[ ! -L ]``; the mock
provider resolves the final path and rejects anything outside its root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ailab_platform.queue import Message, RedisQueue
from ailab_platform.sandbox.base import ExecResult, SandboxError, SandboxProvider
from ailab_platform.sandbox.mock import MockSandbox


class _RecordingProvider(SandboxProvider):
    def __init__(self) -> None:
        self.cmd = ""

    def acquire(self, lease_id: str, image: str, ttl_seconds: int | None = None) -> str:
        return lease_id

    def inspect(self, provider_ref):
        raise NotImplementedError

    def list_owned(self, owner):
        raise NotImplementedError

    def update_metadata(self, provider_ref, labels):
        raise NotImplementedError

    def renew(self, provider_ref, until):
        raise NotImplementedError

    def exec(self, sandbox_id: str, cmd: str, timeout: int) -> ExecResult:
        self.cmd = cmd
        return ExecResult(exit_code=0, stdout="12\n", stderr="", duration_ms=1)

    def put_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        raise NotImplementedError

    def get_file(self, sandbox_id: str, path: str) -> bytes:
        raise NotImplementedError

    def put_file_from_path(self, *args, **kwargs):
        raise NotImplementedError

    def get_file_to_path(self, *args, **kwargs):
        raise NotImplementedError

    def list_files(self, sandbox_id: str, path: str) -> list[dict]:
        raise NotImplementedError

    def release(self, sandbox_id: str) -> None:
        raise NotImplementedError


def test_file_size_command_checks_parent_and_link() -> None:
    provider = _RecordingProvider()
    assert provider.file_size("sandbox", "/workspace/outputs/a b.txt") == 12
    assert "realpath -m" in provider.cmd
    assert "[ ! -L" in provider.cmd
    assert "/workspace/outputs/a b.txt" in provider.cmd


def test_mock_sandbox_rejects_symlink_escape() -> None:
    provider = MockSandbox()
    sandbox_id = provider.acquire("lease-symlink", "unused")
    root = provider._sandbox_root(sandbox_id)
    link = root / "workspace" / "outputs" / "passwd"
    link.symlink_to("/etc/passwd")

    with pytest.raises(SandboxError, match="escapes"):
        provider.file_size(sandbox_id, "/workspace/outputs/passwd")
    with pytest.raises(SandboxError, match="escapes"):
        provider.get_file(sandbox_id, "/workspace/outputs/passwd")
    with pytest.raises(SandboxError, match="escapes"):
        provider.put_file(sandbox_id, "/workspace/outputs/passwd", b"overwrite")


def test_mock_sandbox_keeps_tmp_private(tmp_path) -> None:
    """Render warmup and cleanup write and clear /tmp; on the mock provider that must never reach the host /tmp."""
    provider = MockSandbox()
    sandbox_id = provider.acquire(f"lease-tmp-{tmp_path.name}", "unused")
    root = provider._sandbox_root(sandbox_id)
    marker = f"/tmp/ailab-mock-{tmp_path.name}"
    result = provider.exec(sandbox_id, f'echo x > {marker} && test -f "$TMPDIR/{Path(marker).name}"', 5)
    assert result.exit_code == 0
    assert (root / "tmp" / Path(marker).name).exists() and not Path(marker).exists()
    script = Path(__file__).resolve().parents[1] / "ailab_platform/sandbox_tools/render_sandbox_health.py"
    provider.put_file(sandbox_id, "/workspace/.ailab/render_sandbox_health.py", script.read_bytes())
    # Exec output maps host paths back to sandbox paths, so compare inside the sandbox process.
    probe = (
        "import os, runpy; m = runpy.run_path('/workspace/.ailab/render_sandbox_health.py'); "
        "print(m['WORK'].samefile(os.getcwd()), str(m['TEMP']) == os.environ['TMPDIR'])"
    )
    assert provider.exec(sandbox_id, f'python -c "{probe}"', 10).stdout.split() == ["True", "True"]
    provider.release(sandbox_id)


def test_redis_ack_is_one_atomic_script() -> None:
    class _FakeRedis:
        def __init__(self) -> None:
            self.calls: list[tuple] = []

        def eval(self, script: str, numkeys: int, *args: str) -> int:
            self.calls.append((script, numkeys, args))
            return 1

    q = object.__new__(RedisQueue)
    q.client = _FakeRedis()  # type: ignore[assignment]
    q.ack("ailab:stream:runs", Message(id="1-0", payload={}))

    script, numkeys, args = q.client.calls[0]
    assert "XACK" in script and "XDEL" in script
    assert numkeys == 2
    assert args == ("ailab:stream:runs", "ailab:stream:runs:jobs", "workers", "1-0", "")
