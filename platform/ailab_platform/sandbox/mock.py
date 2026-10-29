"""Local mock sandbox: a temp directory + host subprocess.

Dev/test only — no isolation. It exists so the API/Agent/Broker/Worker can be
exercised before T1/T2 images and OpenSandbox are ready.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..config import BASE_DIR, settings
from .base import SANDBOX_EXPIRY_GRACE_SECONDS, ExecResult, SandboxError, SandboxProvider, SandboxResource, SandboxSpec
from .transfer import CHUNK_SIZE, copy_chunks

SKILLS = BASE_DIR.parent / "images" / "sbx-office" / "skills"
TOOLS = BASE_DIR.parent / "images" / "sbx-office"


class MockSandbox(SandboxProvider):
    name = "mock"

    def __init__(self) -> None:
        self._process_lock = threading.Lock()
        self._processes: dict[str, set[subprocess.Popen]] = {}
        self.root = settings.data_dir / "sandboxes"
        self.root.mkdir(parents=True, exist_ok=True)

    def _sandbox_root(self, sandbox_id: str) -> Path:
        path = (self.root / sandbox_id).resolve()
        if self.root.resolve() not in path.parents:
            raise SandboxError("invalid sandbox id")
        return path

    def _translate(self, sandbox_id: str, path: str) -> Path:
        root = self._sandbox_root(sandbox_id)
        if path == "/workspace":
            return root / "workspace"
        if path.startswith("/workspace/"):
            rel = path[len("/workspace/") :]
            target = (root / "workspace" / rel).resolve()
        elif path.startswith("/opt/skills/"):
            target = (SKILLS / path[len("/opt/skills/") :]).resolve()
        elif path.startswith("/opt/ailab-tools/"):
            target = (TOOLS / path[len("/opt/ailab-tools/") :]).resolve()
        else:
            raise SandboxError(f"mock sandbox only supports /workspace and /opt paths, got {path}")
        if root not in target.parents and target != root and SKILLS not in target.parents and TOOLS not in target.parents:
            raise SandboxError(f"path escapes sandbox: {path}")
        return target

    def acquire(
        self,
        lease_id: str,
        image: str,
        ttl_seconds: int | None = None,
        *,
        spec: SandboxSpec | None = None,
        labels: dict[str, str] | None = None,
    ) -> str:
        root = self._sandbox_root(lease_id)
        (root / "workspace" / "inputs").mkdir(parents=True, exist_ok=True)
        (root / "workspace" / "outputs").mkdir(parents=True, exist_ok=True)
        self.update_metadata(lease_id, labels or {})
        return lease_id

    def inspect(self, provider_ref: str) -> SandboxResource | None:
        root = self._sandbox_root(provider_ref)
        if not root.exists():
            return None
        meta = root / "lease.json"
        return SandboxResource(provider_ref, json.loads(meta.read_text()) if meta.exists() else {})

    def list_owned(self, owner: str) -> list[SandboxResource]:
        resources = (self.inspect(p.name) for p in sorted(self.root.iterdir()) if p.is_dir())
        return [r for r in resources if r and r.labels.get("ailab.owner") == owner]

    def update_metadata(self, provider_ref: str, labels: dict[str, str]) -> None:
        root = self._sandbox_root(provider_ref)
        # Kept outside workspace snapshots. This mock has no security boundary.
        meta = root / "lease.json"
        existing = json.loads(meta.read_text()) if meta.exists() else {}
        temporary = root / "lease.json.tmp"
        temporary.write_text(json.dumps({**existing, **labels}))
        temporary.replace(meta)

    def renew(self, provider_ref: str, until: datetime) -> None:
        self.update_metadata(
            provider_ref,
            {
                "ailab.expires_at": until.isoformat(),
                "ailab.provider_expires_at": (until + timedelta(seconds=SANDBOX_EXPIRY_GRACE_SECONDS)).isoformat(),
            },
        )

    def reap_expired(self) -> int:
        count = 0
        for path in self.root.iterdir():
            if not path.is_dir():
                continue
            resource = self.inspect(path.name)
            expires = resource.labels.get("ailab.provider_expires_at") if resource else None
            if expires and datetime.fromisoformat(expires) <= datetime.now(UTC):
                self.release(path.name)
                count += 1
        return count

    def _translate_command(self, sandbox_id: str, cmd: str) -> str:
        translated = cmd
        root = self._sandbox_root(sandbox_id)
        translated = translated.replace("/tmp/ailab-background", str(root / "background"))
        translated = translated.replace("/workspace", str(root / "workspace"))
        translated = translated.replace("/opt/skills", str(SKILLS))
        translated = translated.replace("/opt/ailab-office", str(BASE_DIR.parent / "vendor" / "zcode-office-runtime"))
        return translated.replace("/opt/ailab-tools", str(TOOLS))

    def exec(self, sandbox_id: str, cmd: str, timeout: int) -> ExecResult:
        translated = self._translate_command(sandbox_id, cmd)
        root = self._sandbox_root(sandbox_id)
        env = os.environ.copy()
        env["PATH"] = f"{Path(sys.executable).parent}:{env.get('PATH', '')}"
        started = time.monotonic()
        try:
            proc = subprocess.Popen(
                translated,
                shell=True,
                cwd=root / "workspace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise SandboxError("mock sandbox unavailable") from exc
        with self._process_lock:
            self._processes.setdefault(sandbox_id, set()).add(proc)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            exit_code = proc.returncode
        except subprocess.TimeoutExpired:
            self._kill_group(proc)
            stdout, stderr = proc.communicate(timeout=5)
            stderr, exit_code = f"timeout after {timeout}s", 124
        finally:
            with self._process_lock:
                self._processes.get(sandbox_id, set()).discard(proc)
                if not self._processes.get(sandbox_id):
                    self._processes.pop(sandbox_id, None)
        host_ws = str(root / "workspace")
        return ExecResult(
            exit_code,
            stdout[-128000:].replace(host_ws, "/workspace"),
            stderr[-128000:].replace(host_ws, "/workspace"),
            int((time.monotonic() - started) * 1000),
        )

    @staticmethod
    def _kill_group(proc: subprocess.Popen) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def cancel_exec(self, provider_ref: str) -> bool:
        with self._process_lock:
            for proc in self._processes.get(provider_ref, set()):
                self._kill_group(proc)
        return True

    def put_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        target = self._translate(sandbox_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.startswith("/workspace/.ailab-exec-") and path.endswith(".json"):
            payload = json.loads(data)
            if isinstance(payload.get("cmd"), str):
                payload["cmd"] = self._translate_command(sandbox_id, payload["cmd"])
                data = json.dumps(payload, ensure_ascii=False).encode()
        target.write_bytes(data)

    def get_file(self, sandbox_id: str, path: str) -> bytes:
        target = self._translate(sandbox_id, path)
        if not target.is_file():
            raise SandboxError(f"file not found in sandbox: {path}")
        return target.read_bytes()

    def put_file_from_path(self, sandbox_id: str, path: str, source: Path, *, max_bytes: int) -> None:
        target = self._translate(sandbox_id, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".upload-{os.urandom(12).hex()}")
        try:
            with source.open("rb") as src, temporary.open("xb") as dst:
                copy_chunks(iter(lambda: src.read(CHUNK_SIZE), b""), dst, max_bytes=max_bytes)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def get_file_to_path(self, sandbox_id: str, path: str, target: Path, *, max_bytes: int) -> None:
        source = self._translate(sandbox_id, path)
        if not source.is_file():
            raise SandboxError("not a regular file in the sandbox")
        with source.open("rb") as src, target.open("wb") as dst:
            copy_chunks(iter(lambda: src.read(CHUNK_SIZE), b""), dst, max_bytes=max_bytes)

    def file_size(self, sandbox_id: str, path: str) -> int:
        target = self._translate(sandbox_id, path)
        if not target.is_file():
            raise SandboxError(f"not a regular file in the sandbox: {path}")
        return target.stat().st_size

    def list_files(self, sandbox_id: str, path: str) -> list[dict]:
        target = self._translate(sandbox_id, path)
        if not target.exists():
            return []
        out: list[dict] = []
        for item in sorted(target.rglob("*")):
            if item.is_file():
                out.append(
                    {
                        "path": "/workspace/" + str(item.relative_to(self._sandbox_root(sandbox_id) / "workspace")),
                        "size": item.stat().st_size,
                    }
                )
        return out[:500]

    def release(self, sandbox_id: str) -> None:
        self.cancel_exec(sandbox_id)
        root = self._sandbox_root(sandbox_id)
        if root.exists():
            # Detached helper processes are outside MockSandbox's exec registry.
            from ..agent.background_helper import alive

            for path in [*root.glob("background/*/meta"), root / "background/reaper"]:
                if path.exists():
                    meta = json.loads(path.read_text())
                    if alive(meta):
                        try:
                            os.killpg(meta["pid"], signal.SIGKILL)
                        except ProcessLookupError:
                            pass
            shutil.rmtree(root)
