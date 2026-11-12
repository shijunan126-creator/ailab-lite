"""OpenSandbox provider (T2 dev server, Docker runtime).

The Broker API stays the same; switching ``AILAB_SANDBOX_PROVIDER=opensandbox``
routes lifecycle/exec/file operations through the OpenSandbox server instead of
talking to Docker directly. On k8s this becomes the controller + ingress-gateway
path, and the per-sandbox token replaces this server-side API key.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import shlex
import threading
import time
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import httpx

from ..config import settings
from ..paths import PathError, normalize_path
from .base import (
    SANDBOX_EXPIRY_GRACE_SECONDS,
    ExecResult,
    SandboxError,
    SandboxProvider,
    SandboxResource,
    SandboxSpec,
    lines_error,
    lines_script,
)
from .transfer import CHUNK_SIZE, TRANSFER_SECONDS, TransferLimitError, TransferReader, copy_chunks


def _checked(path: str) -> str:
    try:
        return normalize_path(path)
    except PathError as exc:
        raise SandboxError(str(exc)) from exc


def _chain(path: str) -> list[str]:
    """/workspace and every component below it down to ``path``: lstat sees a link only on the entry it is asked about."""
    try:
        parts = PurePosixPath(path).relative_to("/workspace").parts
    except ValueError:
        return []
    return ["/workspace"] + ["/workspace/" + "/".join(parts[: i + 1]) for i in range(len(parts))]


def _head(chunks, size: int):
    for chunk in chunks:
        if size <= 0:
            return
        yield chunk[:size]
        size -= len(chunk)


def _join(messages) -> str:
    return "\n".join((m.text or "") for m in messages).strip("\n")


class _ExpectedMiss(logging.Filter):
    """Drop the SDK's ERROR traceback for a file-info lookup of a missing path.

    The SDK logs every failed call with a traceback. A missing path is how the write guard learns
    a file is new, which is most writes, so it would otherwise log a traceback per new file.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        error = record.exc_info[1] if record.exc_info else None
        return not (record.getMessage().startswith("Failed to get file info") and "FILE_NOT_FOUND" in str(error))


_MISS = _ExpectedMiss()


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
        sdk_log = logging.getLogger("opensandbox.sync.adapters.filesystem_adapter")
        if _MISS not in sdk_log.filters:
            sdk_log.addFilter(_MISS)
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
            stdout=_join(execution.logs.stdout)[-128000:],
            stderr=_join(execution.logs.stderr)[-128000:],
            duration_ms=duration,
        )

    # Every exec costs about a second on OpenSandbox (1.1.1, measured), the file API a few
    # milliseconds. The guards below ask the file API first and keep the guarded shell for
    # anything they cannot prove: a link, a missing path or an API failure. Like the shell guard,
    # the check precedes the transfer; the sandbox boundary, not this check, contains a racing agent.
    def _lstat(self, sandbox_id: str, paths: list[str]) -> dict[str, tuple[str, int]] | None:
        """Kind and size of each path, links not followed. None if any path is missing or the API fails."""
        try:
            infos = self._sandbox(sandbox_id).files.get_file_info(paths)
            return {p: ((infos[p].entry_type or "").lower(), int(infos[p].size)) for p in paths}
        except Exception:
            return None

    def _plain_size(self, sandbox_id: str, path: str) -> int | None:
        """The size of a regular file reached through real directories only, or None."""
        chain = _chain(path)
        info = self._lstat(sandbox_id, chain) if len(chain) > 1 else None
        if info is None or any(info[p][0] != "directory" for p in chain[:-1]) or info[path][0] != "file":
            return None
        return info[path][1]

    def _direct_write(self, sandbox_id: str, path: str) -> str | None:
        """Whether the file API may write ``path`` itself: "file" (replace it), "absent" (create it), or None.

        Every parent must already exist as a real directory. A missing one goes through the shell
        guard's ``mkdir -p`` as the sandbox user: execd may run as another user, and directories it
        creates would not be writable by the agent's commands.
        """
        chain = _chain(path)
        if len(chain) < 2:
            return None
        info = self._lstat(sandbox_id, chain)
        if info is None:  # usually a new file; otherwise a missing parent or a failing API, which the shell settles
            info = self._lstat(sandbox_id, chain[:-1])
            if info is None:
                return None
        if any(info[p][0] != "directory" for p in chain[:-1]):
            return None
        return "absent" if path not in info else ("file" if info[path][0] == "file" else None)

    def file_size(self, sandbox_id: str, path: str) -> int:
        size = self._plain_size(sandbox_id, path)
        return size if size is not None else super().file_size(sandbox_id, path)

    def _guard_write(self, sandbox_id: str, path: str) -> str | None:
        """Make ``path`` writable through the file API: the fast check, else the guarded shell. Returns the fast verdict."""
        verdict = self._direct_write(sandbox_id, path)
        if verdict is not None:
            return verdict
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
        return None

    def put_file(self, sandbox_id: str, path: str, data: bytes) -> None:
        path = _checked(path)
        self._guard_write(sandbox_id, path)
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
        from opensandbox.models.filesystem import MoveEntry

        path = _checked(path)
        if source.stat().st_size > max_bytes:
            raise TransferLimitError("file exceeds transfer limit")
        verdict = self._guard_write(sandbox_id, path)
        temporary = str(Path(path).with_name(f".ailab-upload-{uuid.uuid4().hex}"))
        files = self._sandbox(sandbox_id).files
        published = False
        try:
            # SDK 1.1.1 streams binary IOBase in both direct chunked multipart
            # and Content-Length server-proxy mode. Passing bytes defeats this.
            with TransferReader(io.FileIO(source, "rb"), max_bytes) as stream:
                files.write_file(temporary, stream, mode=644)
            if verdict == "absent":  # the file API's move refuses to replace, so only a new file avoids the exec
                try:
                    files.move_files([MoveEntry(src=temporary, dest=path)])
                    published = True
                except Exception:
                    pass  # created meanwhile: replace it below
            if not published:
                moved = self.exec(sandbox_id, f"mv -f -- {shlex.quote(temporary)} {shlex.quote(path)}", timeout=15)
                if moved.exit_code != 0:
                    raise SandboxError("OpenSandbox streaming upload could not publish file")
                published = True
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError("OpenSandbox streaming upload failed") from exc
        finally:
            if not published:  # a failed multipart request may leave a partial sandbox file
                try:
                    files.delete_files([temporary])
                except Exception:
                    # Preserve the transfer error if the resource itself vanished;
                    # lease reconciliation owns resource cleanup in this case.
                    pass

    def get_file_to_path(self, sandbox_id: str, path: str, target: Path, *, max_bytes: int) -> None:
        path = _checked(path)
        if self.file_size(sandbox_id, path) > max_bytes:
            raise TransferLimitError("file exceeds transfer limit")
        self._stream(sandbox_id, path, target, max_bytes=max_bytes)

    def _stream(self, sandbox_id: str, path: str, target: Path, *, max_bytes: int, head: bool = False) -> None:
        """Stream ``path`` to ``target``; ``head`` keeps its first ``max_bytes`` instead of refusing a larger file."""
        try:
            chunks = self._sandbox(sandbox_id).files.read_bytes_stream(path, chunk_size=CHUNK_SIZE)
            with closing(chunks), target.open("wb") as stream:
                copy_chunks(_head(chunks, max_bytes) if head else chunks, stream, max_bytes=max_bytes)
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError("OpenSandbox streaming download failed") from exc

    def get_file_lines_to_path(self, sandbox_id: str, path: str, target: Path, *, line: int, max_bytes: int) -> None:
        path = _checked(path)
        if line == 1 and self._plain_size(sandbox_id, path) is not None:
            self._stream(sandbox_id, path, target, max_bytes=max_bytes, head=True)  # the common read: no exec at all
            return
        # The file API's offset/limit is unreliable (offset 0 is a 400) and exec output is decoded
        # text, so one exec writes the window to a temporary file and the file API streams just that.
        # The file lives outside /workspace: a parallel ``ls`` or a workspace snapshot must never see
        # it. That path is ours, not the model's, so it is streamed without the /workspace guard.
        temp = f"/tmp/.ailab-lines-{uuid.uuid4().hex}"  # hex only: safe unquoted
        try:
            result = self.exec(sandbox_id, f"{{ {lines_script(path, line, max_bytes)}; }} > {temp}", timeout=TRANSFER_SECONDS)
            if result.exit_code:
                raise lines_error(result.exit_code, path)
            self._stream(sandbox_id, temp, target, max_bytes=max_bytes)
        finally:
            try:
                self._sandbox(sandbox_id).files.delete_files([temp])
            except Exception:
                pass  # the read's own error matters more; a vanished sandbox takes its /tmp with it

    def remove_file(self, sandbox_id: str, path: str) -> None:
        try:
            self._sandbox(sandbox_id).files.delete_files([_checked(path)])
        except Exception:
            super().remove_file(sandbox_id, path)  # already gone, or the API failed: the shell settles which

    def list_files(self, sandbox_id: str, path: str) -> list[dict]:
        from .file_listing import LIST_PROGRAM, parse_listing

        path = _checked(path)
        command = " ".join(shlex.quote(v) for v in ["python", "-c", LIST_PROGRAM, path, "list", "500", "0"])
        result = self.exec(sandbox_id, command, timeout=60)
        if result.exit_code != 0:
            raise SandboxError("file enumeration failed")
        return parse_listing(result.stdout)

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
