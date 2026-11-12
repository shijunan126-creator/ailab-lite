"""Sandbox provider interface.

The Broker owns the only handle to a provider; the Agent never talks to the
container runtime directly. In production this maps to OpenSandbox's
ingress-gateway + per-sandbox token.
"""

from __future__ import annotations

import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..contracts.sandbox import LeaseHandle as LeaseHandle
from ..contracts.sandbox import SandboxResource as SandboxResource
from ..contracts.sandbox import SandboxSpec as SandboxSpec


class SandboxError(RuntimeError):
    pass


class SandboxFileNotFound(SandboxError):
    """The path is not in the sandbox at all.

    Its own class because the Broker maps it to 404: telling the Agent "400" made every failure look
    like a missing file, and a model that hears ENOENT rewrites the file instead of reading it.
    """


# The provider-side expiry outlives the Broker lease by this much, so the Broker
# reaper normally acts first and the provider is only the backstop.
SANDBOX_EXPIRY_GRACE_SECONDS = 300


# Exit codes of ``lines_script``. Kept clear of 1 and 125-127, which `docker exec` and shells use
# for their own failures (a vanished container must not read as "outside the workspace").
LINES_REFUSED, LINES_ABSENT, LINES_NOT_A_FILE = 10, 11, 12


def lines_script(path: str, line: int, max_bytes: int) -> str:
    """Shell printing at most ``max_bytes`` of ``path`` from 1-indexed ``line`` on.

    Same guard as ``file_size``: the parent must resolve inside ``/workspace`` and the file itself
    must not be a link, since ``[ -f ]`` and ``tail`` follow links out of the workspace.
    ``tail -n +N`` streams to the line inside the sandbox; ``head -c`` stops it at the window.
    """
    if line < 1 or max_bytes < 1:
        raise SandboxError(f"invalid window line={line} max_bytes={max_bytes}")
    q = shlex.quote(path)
    body = f"head -c {max_bytes} -- {q}" if line == 1 else f"tail -n +{line} -- {q} | head -c {max_bytes}"
    return (
        f"parent=$(dirname {q}); "
        f'rp=$(realpath -m "$parent") || exit {LINES_REFUSED}; '
        f'case "$rp" in /workspace|/workspace/*) ;; *) exit {LINES_REFUSED};; esac; '
        f"[ ! -L {q} ] || exit {LINES_REFUSED}; "
        f"[ -e {q} ] || exit {LINES_ABSENT}; "
        f"[ -f {q} ] && [ -r {q} ] || exit {LINES_NOT_A_FILE}; "
        f"{body}"
    )


def lines_error(code: int, path: str) -> SandboxError:
    """The error a failed ``lines_script`` stands for."""
    if code == LINES_ABSENT:
        return SandboxFileNotFound(f"no such file in the sandbox: {path}")
    if code == LINES_NOT_A_FILE:
        return SandboxError(f"not a regular file in the sandbox: {path}")
    if code == LINES_REFUSED:
        return SandboxError(f"refusing to read through a link or outside /workspace: {path}")
    return SandboxError(f"could not read {path} (exit {code})")


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class SandboxProvider(ABC):
    name = "base"
    # Render sandboxes keep a private LibreOffice listener warm and convert through it. A provider whose
    # commands run on the host (mock) has no private listener; its conversions start LibreOffice per call.
    office_listener = True

    @abstractmethod
    def acquire(
        self,
        lease_id: str,
        image: str,
        ttl_seconds: int | None = None,
        *,
        spec: SandboxSpec | None = None,
        labels: dict[str, str] | None = None,
    ) -> str:
        """Create a resource with ownership metadata before it becomes discoverable."""

    @abstractmethod
    def inspect(self, provider_ref: str) -> SandboxResource | None:
        """None means confirmed absent; dependency failures must raise SandboxError."""

    @abstractmethod
    def list_owned(self, owner: str) -> list[SandboxResource]: ...

    @abstractmethod
    def update_metadata(self, provider_ref: str, labels: dict[str, str]) -> None:
        """Trusted control-plane metadata, inaccessible to user commands."""

    @abstractmethod
    def renew(self, provider_ref: str, until: datetime) -> None:
        """Extend the provider backstop to until + expiry grace, where supported."""

    def cancel_exec(self, provider_ref: str) -> bool:
        """False means cancellation requires releasing the entire sandbox."""
        return False

    @abstractmethod
    def exec(self, sandbox_id: str, cmd: str, timeout: int) -> ExecResult:
        """Run cmd with bash in /workspace; every backend uses the same shell."""

    @abstractmethod
    def put_file(self, sandbox_id: str, path: str, data: bytes) -> None: ...

    @abstractmethod
    def get_file(self, sandbox_id: str, path: str) -> bytes: ...

    @abstractmethod
    def put_file_from_path(self, sandbox_id: str, path: str, source: Path, *, max_bytes: int) -> None:
        """Upload a bounded local file without materializing it as bytes."""

    @abstractmethod
    def get_file_to_path(self, sandbox_id: str, path: str, target: Path, *, max_bytes: int) -> None:
        """Download with an enforced byte limit; callers discard partial local output on error."""

    @abstractmethod
    def list_files(self, sandbox_id: str, path: str) -> list[dict]: ...

    @abstractmethod
    def release(self, sandbox_id: str) -> None: ...

    @abstractmethod
    def get_file_lines_to_path(self, sandbox_id: str, path: str, target: Path, *, line: int, max_bytes: int) -> None:
        """Write at most ``max_bytes`` of a sandbox file, from 1-indexed ``line`` on, to ``target``.

        ``read`` shows a 50 KB window of a file that may be 50 MB: the sandbox seeks to the line
        and only the window leaves it. Each provider moves the bytes its own way; the guarded
        shell is shared (``lines_script``) so every backend refuses the same paths.
        """

    def file_size(self, sandbox_id: str, path: str) -> int:
        """Size of a regular file, without transferring it.

        ``[ -f ]`` and ``stat`` follow symlinks, so a link created by the agent
        could point outside ``/workspace``. Resolve the parent and reject the
        link before reading. (A determined agent could still race the check;
        the provider boundary is the real security boundary.)
        """
        q = shlex.quote(path)
        cmd = (
            f"parent=$(dirname {q}); "
            'rp=$(realpath -m "$parent") || exit 1; '
            'case "$rp" in /workspace|/workspace/*) ;; *) exit 1;; esac; '
            f"[ ! -L {q} ] || exit 1; "
            f"[ -e {q} ] || exit 2; "  # 2 = absent (404), 3 = present but not a regular file (400)
            f"[ -f {q} ] || exit 3; "
            f"stat -c %s {q}"
        )
        res = self.exec(sandbox_id, cmd, timeout=15)
        if res.exit_code == 2:
            raise SandboxFileNotFound(f"no such file in the sandbox: {path}")
        if res.exit_code != 0 or not res.stdout.strip().isdigit():
            raise SandboxError(f"not a regular file in the sandbox: {path}")
        return int(res.stdout.strip())

    def remove_file(self, sandbox_id: str, path: str) -> None:
        """Delete a file the platform wrote (never a model-chosen path); a missing one is not an error."""
        self.exec(sandbox_id, f"rm -f -- {shlex.quote(path)}", timeout=30)

    def reap_expired(self) -> int:
        """Remove sandboxes past their provider-side expiry; returns how many."""
        return 0

    def health(self) -> bool:
        return True
