"""OpenSandbox provider (T2 dev server, Docker runtime).

The Broker API stays the same; switching ``AILAB_SANDBOX_PROVIDER=opensandbox``
routes lifecycle/exec/file operations through the OpenSandbox server instead of
talking to Docker directly. On k8s this becomes the controller + ingress-gateway
path, and the per-sandbox token replaces this server-side API key.
"""

from __future__ import annotations

import base64
import io
import re
import shlex
import threading
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from ..config import settings
from ..paths import PathError, normalize_path
from .base import SANDBOX_EXPIRY_GRACE_SECONDS, ExecResult, SandboxError, SandboxProvider, SandboxResource, SandboxSpec
from .transfer import CHUNK_SIZE, TransferLimitError, TransferReader, copy_chunks


def _checked(path: str) -> str:
    try:
        return normalize_path(path)
    except PathError as exc:
        raise SandboxError(str(exc)) from exc


def _join(messages) -> str:
    return "\n".join((m.text or "") for m in messages).strip("\n")


_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,61}[A-Za-z0-9])?\Z")
_ENCODED = re.compile(r"b32\.(\d+)\Z")
_CHUNK = re.compile(r"\.b32-\d+\Z")


def _encode_metadata(labels: dict[str, str]) -> dict[str, str]:
    # OSB validates metadata as Kubernetes labels, including timestamps and JSON.
    # Keep ordinary identity labels readable/filterable; encode other values per
    # field so a metadata PATCH cannot overwrite unrelated concurrent changes.
    result = {}
    for key, value in labels.items():
        if _LABEL.fullmatch(value) and not _ENCODED.fullmatch(value):
            result[key] = value
            continue
        encoded = base64.b32encode(value.encode()).decode().rstrip("=")
        chunks = [encoded[i : i + 63] for i in range(0, len(encoded), 63)]
        result[key] = f"b32.{len(chunks)}"
        result.update({f"{key}.b32-{i}": chunk for i, chunk in enumerate(chunks)})
    return result


def _decode_metadata(labels: dict[str, str]) -> dict[str, str]:
    result = {}
    for key, value in labels.items():
        if _CHUNK.search(key):
            continue
        marker = _ENCODED.fullmatch(value)
        if marker:
            try:
                encoded = "".join(labels[f"{key}.b32-{i}"] for i in range(int(marker[1])))
                value = base64.b32decode(encoded + "=" * (-len(encoded) % 8)).decode()
            except (KeyError, ValueError, UnicodeError) as exc:
                raise SandboxError("invalid OpenSandbox lease metadata") from exc
        result[key] = value
    return result


class OpenSandboxProvider(SandboxProvider):
    name = "opensandbox"

    def __init__(self) -> None:
        try:
            from opensandbox import SandboxManagerSync, SandboxSync
            from opensandbox.config import ConnectionConfigSync
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SandboxError(
                "opensandbox SDK is not installed; run `pip install opensandbox==1.1.1` or use AILAB_SANDBOX_PROVIDER=docker"
            ) from exc

        self._SandboxSync = SandboxSync
        self._config = ConnectionConfigSync(
            domain=settings.osb_domain,
            protocol=settings.osb_protocol,
            api_key=settings.osb_api_key,
            use_server_proxy=settings.osb_use_server_proxy,
            request_timeout=timedelta(seconds=settings.osb_request_timeout_seconds),
        )
        self._manager = SandboxManagerSync.create(connection_config=self._config)
        self._cache: dict[str, object] = {}
        self._executions: dict[str, set[str]] = {}
        self._lock = threading.Lock()

    def _remember(self, sandbox) -> str:
        with self._lock:
            self._cache[sandbox.id] = sandbox
        return sandbox.id

    def _sandbox(self, sandbox_id: str):
        with self._lock:
            cached = self._cache.get(sandbox_id)
        if cached is not None:
            return cached
        try:
            sandbox = self._SandboxSync.connect(sandbox_id, connection_config=self._config)
        except Exception as exc:
            raise SandboxError(f"could not connect to sandbox {sandbox_id}: {exc}") from exc
        with self._lock:
            self._cache[sandbox_id] = sandbox
        return sandbox

    def acquire(
        self,
        lease_id: str,
        image: str,
        ttl_seconds: int | None = None,
        *,
        spec: SandboxSpec | None = None,
        labels: dict[str, str] | None = None,
    ) -> str:
        from opensandbox.models import NetworkPolicy

        # OpenSandbox's timeout is an absolute expiry (extended only via renew()),
        # not an idle timeout, so it must cover the whole lease.
        lifetime = (ttl_seconds or settings.sandbox_ttl_seconds) + SANDBOX_EXPIRY_GRACE_SECONDS

        spec = spec or SandboxSpec(image=image, ttl=ttl_seconds or settings.sandbox_ttl_seconds)
        try:
            sandbox = self._SandboxSync.create(
                image or settings.sandbox_image,
                connection_config=self._config,
                timeout=timedelta(seconds=lifetime),
                ready_timeout=timedelta(seconds=90),
                # No retry without a sidecar policy: only an explicit deployment
                # mode may delegate isolation to Kubernetes and its node policy.
                network_policy=NetworkPolicy(default_action="deny") if settings.osb_network_mode == "sidecar" else None,
                resource={"cpu": str(spec.cpu), "memory": str(spec.memory)},
                metadata=_encode_metadata(labels or {}),
            )
        except Exception as exc:
            raise SandboxError(f"OpenSandbox create failed: {exc}") from exc
        return self._remember(sandbox)

    def exec(self, sandbox_id: str, cmd: str, timeout: int) -> ExecResult:
        from opensandbox.models.execd import RunCommandOpts
        from opensandbox.models.execd_sync import ExecutionHandlersSync

        if isinstance(cmd, (list, tuple)):
            cmd = " ".join(shlex.quote(str(part)) for part in cmd)
        sandbox = self._sandbox(sandbox_id)
        execution_ids: set[str] = set()

        def remember(event):
            execution_ids.add(event.id)
            with self._lock:
                self._executions.setdefault(sandbox_id, set()).add(event.id)

        try:
            execution = sandbox.commands.run(
                cmd,
                opts=RunCommandOpts(working_directory="/workspace", timeout=timedelta(seconds=timeout)),
                handlers=ExecutionHandlersSync(on_init=remember),
            )
        except Exception as exc:
            raise SandboxError("OpenSandbox exec failed") from exc
        finally:
            with self._lock:
                self._executions.get(sandbox_id, set()).difference_update(execution_ids)
                if not self._executions.get(sandbox_id):
                    self._executions.pop(sandbox_id, None)
        exit_code = execution.exit_code
        if exit_code is None:
            exit_code = 1 if execution.error else 0
        duration = 0
        if execution.complete is not None:
            duration = int(execution.complete.execution_time_in_millis)
        return ExecResult(
            exit_code=int(exit_code),
            stdout=_join(execution.logs.stdout)[-64000:],
            stderr=_join(execution.logs.stderr)[-64000:],
            duration_ms=duration,
        )

    def put_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        path = _checked(path)
        q = shlex.quote(path)
        check = self.exec(
            sandbox_id,
            f'p={q}; parent=$(dirname "$p"); '
            'rp=$(realpath -m "$parent") || exit 1; '
            'case "$rp" in /workspace|/workspace/*) ;; *) exit 1;; esac; '
            '[ ! -L "$p" ] && mkdir -p -- "$parent"',
            timeout=15,
        )
        if check.exit_code != 0:
            raise SandboxError(f"refusing to write through a symlink: {path}")
        sandbox = self._sandbox(sandbox_id)
        try:
            # OpenSandbox reads the mode's decimal digits as octal ("644" -> rw-r--r--); 0o644 (= 420) would become r---w----
            sandbox.files.write_file(path, data, mode=644)
        except Exception as exc:
            raise SandboxError(f"OpenSandbox write_file failed: {exc}") from exc

    def get_file(self, sandbox_id: str, path: str) -> bytes:
        path = _checked(path)
        sandbox = self._sandbox(sandbox_id)
        try:
            return bytes(sandbox.files.read_bytes(path))
        except Exception as exc:
            raise SandboxError(f"OpenSandbox read_bytes failed: {exc}") from exc

    def put_file_from_path(self, sandbox_id: str, path: str, source: Path, *, max_bytes: int) -> None:
        path = _checked(path)
        if source.stat().st_size > max_bytes:
            raise TransferLimitError("file exceeds transfer limit")
        q = shlex.quote(path)
        check = self.exec(
            sandbox_id,
            f'p={q}; parent=$(dirname "$p"); rp=$(realpath -m "$parent") || exit 1; '
            'case "$rp" in /workspace|/workspace/*) ;; *) exit 1;; esac; [ ! -L "$p" ] && mkdir -p -- "$parent"',
            timeout=15,
        )
        if check.exit_code != 0:
            raise SandboxError("refusing to write through a symlink")
        temporary = str(Path(path).with_name(f".ailab-upload-{uuid.uuid4().hex}"))
        try:
            # SDK 1.1.1 streams binary IOBase in both direct chunked multipart
            # and Content-Length server-proxy mode. Passing bytes defeats this.
            with TransferReader(io.FileIO(source, "rb"), max_bytes) as stream:
                self._sandbox(sandbox_id).files.write_file(temporary, stream, mode=644)
            moved = self.exec(sandbox_id, f"mv -f -- {shlex.quote(temporary)} {q}", timeout=15)
            if moved.exit_code != 0:
                raise SandboxError("OpenSandbox streaming upload could not publish file")
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError("OpenSandbox streaming upload failed") from exc
        finally:
            # A failed multipart request may leave a partial sandbox file.
            try:
                self.exec(sandbox_id, f"rm -f -- {shlex.quote(temporary)}", timeout=15)
            except SandboxError:
                # Preserve the transfer error if the resource itself vanished;
                # lease reconciliation owns resource cleanup in this case.
                pass

    def get_file_to_path(self, sandbox_id: str, path: str, target: Path, *, max_bytes: int) -> None:
        path = _checked(path)
        if self.file_size(sandbox_id, path) > max_bytes:
            raise TransferLimitError("file exceeds transfer limit")
        try:
            chunks = self._sandbox(sandbox_id).files.read_bytes_stream(path, chunk_size=CHUNK_SIZE)
            with closing(chunks), target.open("wb") as stream:
                copy_chunks(chunks, stream, max_bytes=max_bytes)
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError("OpenSandbox streaming download failed") from exc

    def list_files(self, sandbox_id: str, path: str) -> list[dict]:
        path = _checked(path)
        result = self.exec(
            sandbox_id,
            f"find {shlex.quote(path)} -maxdepth 2 -type f -printf '%p\\t%s\\n'",
            timeout=30,
        )
        if result.exit_code != 0:
            return []
        out: list[dict] = []
        for line in result.stdout.splitlines():
            if "\t" not in line:
                continue
            item_path, size = line.split("\t", 1)
            try:
                out.append({"path": item_path, "size": int(size)})
            except ValueError:
                continue
        return out[:500]

    def cancel_exec(self, provider_ref: str) -> bool:
        with self._lock:
            execution_ids = list(self._executions.get(provider_ref, set()))
        try:
            for execution_id in execution_ids:
                self._sandbox(provider_ref).commands.interrupt(execution_id)
        except Exception as exc:
            raise SandboxError("OpenSandbox interrupt failed") from exc
        return True

    def _forget(self, provider_ref: str) -> None:
        with self._lock:
            sandbox = self._cache.pop(provider_ref, None)
        if sandbox is not None:
            sandbox.close()

    @staticmethod
    def _absent(exc: Exception) -> bool:
        # Only a lifecycle 404 proves absence. Never treat a connection failure as deletion.
        return getattr(exc, "status_code", None) == 404

    def inspect(self, provider_ref: str) -> SandboxResource | None:
        try:
            info = self._manager.get_sandbox_info(provider_ref)
        except Exception as exc:
            if self._absent(exc):
                self._forget(provider_ref)
                return None
            raise SandboxError("OpenSandbox inspect failed") from exc
        return SandboxResource(info.id, _decode_metadata(info.metadata or {}), info.status.state.lower() == "running")

    def list_owned(self, owner: str) -> list[SandboxResource]:
        from opensandbox.models import SandboxFilter

        resources = []
        # Pagination is bounded by a deadline as well as the server's last page.
        deadline, page = time.monotonic() + settings.osb_request_timeout_seconds, 1
        while time.monotonic() < deadline:
            try:
                result = self._manager.list_sandbox_infos(
                    SandboxFilter(metadata=_encode_metadata({"ailab.owner": owner}), page_size=100, page=page)
                )
            except Exception as exc:
                raise SandboxError("OpenSandbox list failed") from exc
            resources.extend(
                SandboxResource(i.id, _decode_metadata(i.metadata or {}), i.status.state.lower() == "running") for i in result.sandbox_infos
            )
            if not result.pagination.has_next_page:
                return resources
            page += 1
        raise SandboxError("OpenSandbox list deadline exceeded")

    def update_metadata(self, provider_ref: str, labels: dict[str, str]) -> None:
        try:
            self._manager.patch_sandbox_metadata(provider_ref, _encode_metadata(labels))
        except Exception as exc:
            raise SandboxError("OpenSandbox metadata update failed") from exc

    def renew(self, provider_ref: str, until: datetime) -> None:
        try:
            self._manager.renew_sandbox(provider_ref, until - datetime.now(UTC) + timedelta(seconds=SANDBOX_EXPIRY_GRACE_SECONDS))
        except Exception as exc:
            raise SandboxError("OpenSandbox renew failed") from exc

    def release(self, sandbox_id: str) -> None:
        try:
            self._manager.kill_sandbox(sandbox_id)
        except Exception as exc:
            if not self._absent(exc):
                raise SandboxError("OpenSandbox delete failed") from exc
        self._forget(sandbox_id)

    def health(self) -> bool:
        try:
            resp = httpx.get(
                f"{settings.osb_protocol}://{settings.osb_domain}/health",
                headers={"OPEN-SANDBOX-API-KEY": settings.osb_api_key},
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False
