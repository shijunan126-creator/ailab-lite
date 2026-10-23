"""Docker-backed sandbox (runc, no network).

This is the dev stand-in for T2's OpenSandbox + gVisor path: same image, same
workspace contract, same Broker API. It deliberately uses ``--network none``
and the same 1 CPU / 1.5 GiB limits as the sandbox overlay.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime

from ..config import settings
from ..paths import PathError, normalize_path
from .base import SANDBOX_EXPIRY_GRACE_SECONDS, ExecResult, SandboxError, SandboxProvider, SandboxResource, SandboxSpec

_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]")


def _run(args: list[str], timeout: int | None = None, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, input=input_bytes)
    except FileNotFoundError as exc:
        raise SandboxError("docker CLI not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"docker command timed out: {' '.join(args[:4])}") from exc


class DockerSandbox(SandboxProvider):
    name = "docker"

    def __init__(self) -> None:
        self.image = settings.sandbox_image

    def _name(self, lease_id: str) -> str:
        return "ailab-sbx-" + _NAME_RE.sub("-", lease_id)[:48]

    def acquire(
        self,
        lease_id: str,
        image: str,
        ttl_seconds: int | None = None,
        *,
        spec: SandboxSpec | None = None,
        labels: dict[str, str] | None = None,
    ) -> str:
        name = self._name(lease_id)
        expires = int(time.time()) + (ttl_seconds or settings.sandbox_ttl_seconds) + SANDBOX_EXPIRY_GRACE_SECONDS
        spec = spec or SandboxSpec(image=image, ttl=ttl_seconds or settings.sandbox_ttl_seconds)
        args = [
            "docker",
            "run",
            "-d",
            "--name",
            name,
            "--label",
            f"ailab.lease={lease_id}",
            "--label",
            f"ailab.expires={expires}",
            "--network",
            "none",
            "--memory",
            str(spec.memory),
            "--memory-swap",
            str(spec.memory),
            "--cpus",
            str(spec.cpu),
            "--pids-limit",
            "256",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "10001",
            "--tmpfs",
            "/tmp:rw,size=256m",
            image or self.image,
        ]
        # Labels survive Broker/Redis restarts. Docker labels are immutable; mutable
        # state/expiry uses a root-only file under /run, never the user workspace.
        for key, value in (labels or {}).items():
            args[-1:-1] = ["--label", f"{key}={value}"]
        proc = _run(args, timeout=120)
        if proc.returncode != 0:
            raise SandboxError(f"docker run failed: {proc.stderr.decode('utf-8', 'replace')[-800:]}")
        for _ in range(40):
            state = _run(["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=10)
            if state.returncode == 0 and state.stdout.decode().strip() == "true":
                return proc.stdout.decode().strip() or name
            time.sleep(0.25)
        logs = _run(["docker", "logs", "--tail", "30", name], timeout=10)
        raise SandboxError(f"sandbox did not start: {logs.stderr.decode('utf-8', 'replace')[-800:]}")

    def exec(self, sandbox_id: str, cmd: str, timeout: int) -> ExecResult:
        started = time.monotonic()
        args = [
            "docker",
            "exec",
            "-i",
            "-u",
            "10001",
            "-w",
            "/workspace",
            sandbox_id,
            "timeout",
            str(timeout),
            "sh",
            "-c",
            cmd,
        ]
        proc = _run(args, timeout=timeout + 20)
        return ExecResult(
            exit_code=proc.returncode,
            stdout=proc.stdout.decode("utf-8", "replace")[-8000:],
            stderr=proc.stderr.decode("utf-8", "replace")[-8000:],
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _check_path(self, path: str) -> str:
        try:
            return normalize_path(path)
        except PathError as exc:
            raise SandboxError(str(exc)) from exc

    def put_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        path = self._check_path(path)
        args = [
            "docker",
            "exec",
            "-i",
            "-u",
            "10001",
            "-w",
            "/workspace",
            sandbox_id,
            "sh",
            "-c",
            'p="$1"; parent=$(dirname "$p"); '
            'rp=$(realpath -m "$parent") || exit 1; '
            'case "$rp" in /workspace|/workspace/*) ;; *) exit 1;; esac; '
            '[ ! -L "$p" ] || exit 1; '
            'mkdir -p "$parent" && cat > "$p"',
            "sh",
            path,
        ]
        proc = _run(args, timeout=60, input_bytes=data)
        if proc.returncode != 0:
            raise SandboxError(f"put_file failed: {proc.stderr.decode('utf-8', 'replace')[-400:]}")

    def get_file(self, sandbox_id: str, path: str) -> bytes:
        path = self._check_path(path)
        proc = _run(["docker", "exec", "-u", "10001", sandbox_id, "cat", path], timeout=60)
        if proc.returncode != 0:
            raise SandboxError(f"get_file failed: {proc.stderr.decode('utf-8', 'replace')[-400:]}")
        return proc.stdout

    def list_files(self, sandbox_id: str, path: str) -> list[dict]:
        path = self._check_path(path)
        proc = _run(
            ["docker", "exec", "-u", "10001", sandbox_id, "find", path, "-maxdepth", "2", "-type", "f", "-printf", "%p\t%s\n"],
            timeout=30,
        )
        if proc.returncode != 0:
            return []
        out = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            if "\t" not in line:
                continue
            p, size = line.split("\t", 1)
            try:
                out.append({"path": p, "size": int(size)})
            except ValueError:
                continue
        return out[:500]

    def inspect(self, provider_ref: str) -> SandboxResource | None:
        proc = _run(["docker", "inspect", provider_ref], timeout=20)
        if proc.returncode != 0:
            if any(s in proc.stderr.decode("utf-8", "replace").lower() for s in ("no such object:", "no such container:")):
                return None
            raise SandboxError("docker inspect failed")
        info = json.loads(proc.stdout)[0]
        labels = info["Config"].get("Labels") or {}
        running = info["State"]["Running"]
        if running:
            meta = _run(["docker", "exec", "-u", "0", provider_ref, "cat", "/run/ailab-lease.json"], timeout=20)
            if meta.returncode == 0:
                labels.update(json.loads(meta.stdout))
            elif b"No such file" not in meta.stderr:
                raise SandboxError("docker lease metadata unavailable")
        return SandboxResource(info["Id"], labels, running)

    def list_owned(self, owner: str) -> list[SandboxResource]:
        proc = _run(["docker", "ps", "-aq", "--no-trunc", "--filter", f"label=ailab.owner={owner}"], timeout=20)
        if proc.returncode != 0:
            raise SandboxError("docker list failed")
        return [resource for ref in proc.stdout.decode().split() if (resource := self.inspect(ref)) is not None]

    def update_metadata(self, provider_ref: str, labels: dict[str, str]) -> None:
        current = self.inspect(provider_ref)
        if current is None:
            raise SandboxError("sandbox disappeared")
        data = json.dumps({**current.labels, **labels}).encode()
        proc = _run(
            [
                "docker",
                "exec",
                "-i",
                "-u",
                "0",
                provider_ref,
                "sh",
                "-c",
                "umask 077; cat > /run/ailab-lease.json.tmp && mv /run/ailab-lease.json.tmp /run/ailab-lease.json",
            ],
            timeout=20,
            input_bytes=data,
        )
        if proc.returncode != 0:
            raise SandboxError("docker metadata update failed")

    def renew(self, provider_ref: str, until: datetime) -> None:
        # Docker has no native expiration. Broker reconciliation enforces this deadline.
        self.update_metadata(provider_ref, {"ailab.expires_at": until.isoformat()})

    def release(self, sandbox_id: str) -> None:
        proc = _run(["docker", "rm", "-f", sandbox_id], timeout=60)
        if proc.returncode != 0 and not any(
            s in proc.stderr.decode("utf-8", "replace").lower() for s in ("no such object:", "no such container:")
        ):
            raise SandboxError("docker delete failed")

    def health(self) -> bool:
        proc = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
        return proc.returncode == 0
