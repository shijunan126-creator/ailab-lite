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

from ..contracts.sandbox import LeaseHandle as LeaseHandle
from ..contracts.sandbox import SandboxResource as SandboxResource
from ..contracts.sandbox import SandboxSpec as SandboxSpec


class SandboxError(RuntimeError):
    pass


# The provider-side expiry outlives the Broker lease by this much, so the Broker
# reaper normally acts first and the provider is only the backstop.
SANDBOX_EXPIRY_GRACE_SECONDS = 300


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class SandboxProvider(ABC):
    name = "base"

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
    def exec(self, sandbox_id: str, cmd: str, timeout: int) -> ExecResult: ...

    @abstractmethod
    def put_file(self, sandbox_id: str, path: str, data: bytes) -> None: ...

    @abstractmethod
    def get_file(self, sandbox_id: str, path: str) -> bytes: ...

    @abstractmethod
    def list_files(self, sandbox_id: str, path: str) -> list[dict]: ...

    @abstractmethod
    def release(self, sandbox_id: str) -> None: ...

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
            f"[ -f {q} ] || exit 1; "
            f"stat -c %s {q}"
        )
        res = self.exec(sandbox_id, cmd, timeout=15)
        if res.exit_code != 0 or not res.stdout.strip().isdigit():
            raise SandboxError(f"not a regular file in the sandbox: {path}")
        return int(res.stdout.strip())

    def reap_expired(self) -> int:
        """Remove sandboxes past their provider-side expiry; returns how many."""
        return 0

    def health(self) -> bool:
        return True
