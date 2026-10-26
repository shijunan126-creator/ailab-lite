"""Sandbox Broker: lease lifecycle, input injection and two-phase output commit."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import mimetypes
import re
import shlex
import shutil
import tempfile
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from .. import metrics
from ..adapters.lease_store_redis import RedisLeaseStore
from ..clients import ClientError, PlatformClient
from ..config import settings
from ..domain.admission import AdmissionDecision, AdmissionPolicy, LeasePrincipal
from ..paths import OUTPUTS, PathError, normalize_path
from ..ports.capacity import CapacityProvider, StaticCapacityProvider
from ..ports.lease_store import LIVE_STATES, LeaseStore
from ..sandbox import ExecResult, SandboxError, get_provider
from ..sandbox.base import LeaseHandle, SandboxResource, SandboxSpec
from ..sandbox.transfer import CHUNK_SIZE, TransferLimitError, copy_chunks
from ..sandbox_tools import RENDER_PREVIEW
from ..storage import get_storage
from ..telemetry import span

log = logging.getLogger("ailab.broker")

# Platform-owned files inside the sandbox live here; they are left out of workspace snapshots.
TOOL_DIR = "/workspace/.ailab"
SNAPSHOT_PATH = f"{TOOL_DIR}/snapshot.tgz"
PREVIEW_SCRIPT = f"{TOOL_DIR}/render_preview.py"
PREVIEW_PATTERNS = ("*.pptx", "*.docx", "*.xlsx", "*.pdf")
MAX_PREVIEWS_PER_CALL = 3
EVICT_REASON = "evicted_idle"
EVICT_SAVE_SECONDS = 600  # reconciliation leaves a sandbox being saved alone this long
EVICT_QUIET_SECONDS = 30  # wait for an operation already in flight before the workspace is packed
EVICT_RETRY_SECONDS = 2.0  # how often a queued request looks for something to reclaim


class LeaseError(RuntimeError):
    code = "lease_provider_error"
    recoverable = True

    def detail(self) -> dict:
        return {"code": self.code, "message": str(self), "recoverable": self.recoverable}


class LeaseLostError(LeaseError):
    code = "lease_lost"


class LeaseExpiredError(LeaseLostError):
    code = "lease_expired"


class LeaseConflictError(LeaseError):
    code = "lease_conflict"


class LeaseCanceledError(LeaseError):
    code = "run_canceled"
    recoverable = False


class BrokerOwnershipError(LeaseError):
    code = "broker_ownership_lost"


class CapacityError(LeaseError):
    """Typed admission failure; never infer capacity from provider error text."""

    code = "sandbox_admission_rejected"


class AdmissionRejected(CapacityError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"sandbox capacity: {reason}")

    def detail(self) -> dict:
        return {**super().detail(), "reason": self.reason}


class AdmissionTimeout(AdmissionRejected):
    code = "sandbox_queue_deadline"


class ArtifactRejected(RuntimeError):
    """The sandbox file cannot be delivered (too large, wrong place)."""


def _safe_name(name: str) -> str:
    return (Path(name.replace("\\", "/")).name or "file")[:200]


def _expired(lease: dict) -> bool:
    return min(
        datetime.fromisoformat(lease["expires_at"]), datetime.fromisoformat(lease.get("max_expires_at", lease["expires_at"]))
    ) <= datetime.now(UTC)


def _unique_name(name: str, used: set[str]) -> str:
    """Two inputs may share a filename; keep both (a.txt, a (2).txt, ...)."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 1
    while candidate in used:
        n += 1
        candidate = f"{stem} ({n}){dot}{ext}"
    used.add(candidate)
    return candidate


def _operation(method):
    """Calls an agent makes on a lease count as activity, and an in-flight one makes it unreclaimable.

    Renewals, status reads and releases are deliberately not wrapped: renewals must not reset the idle
    clock (a long final answer would never look idle), but they are recorded separately — their freshness
    extends the reclaim floor for a lease whose run is still mid-turn (see _evict_floor).
    """

    @functools.wraps(method)
    def wrapper(self, handle, *args, **kwargs):
        lease_id = self._id(handle)
        self._operation_started(lease_id)
        try:
            return method(self, handle, *args, **kwargs)
        finally:
            self._operation_finished(lease_id)

    return wrapper


class LeaseManager:
    def __init__(
        self,
        provider=None,
        platform=None,
        storage=None,
        redis_client=None,
        *,
        store: LeaseStore | None = None,
        capacity: CapacityProvider | None = None,
        start_background: bool = True,
    ) -> None:
        self.provider = provider or get_provider()
        self.platform = platform or PlatformClient(service="broker")
        self.storage = storage or get_storage()
        self.redis = (
            redis_client
            if redis_client is not None
            else redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)
        )
        self.owner = settings.broker_owner
        self._transfer_root = settings.data_dir / "broker-transfers" / hashlib.sha256(self.owner.encode()).hexdigest()[:20]
        self.store = store if store is not None else RedisLeaseStore(self.redis, self.owner)
        self.capacity = capacity or StaticCapacityProvider()
        self._condition = threading.Condition(threading.RLock())
        self._stripes = [threading.RLock() for _ in range(64)]
        self._reconcile_lock = threading.Lock()
        self._start_lock = threading.RLock()
        self._token = uuid.uuid4().hex
        self._start_background = start_background
        self._recovering = False
        self._generation = 0
        self._started = False
        self._fenced = False
        self._inflight: set[str] = set()
        self._pending_runs: set[str] = set()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.admission = AdmissionPolicy(
            self.capacity,
            self.active_leases,
            lambda: settings.sandbox_max_per_user,
            lambda: settings.sandbox_queue_limit,
            lambda: settings.sandbox_render_max_ttl_seconds,
            lambda: settings.sandbox_burst_watermark,
        )
        # Activity lives in this process only: the Broker is the single owner of every lease, and a write
        # per command would contend with renewals on the lease record. After a restart every lease starts
        # a fresh idle period, so nothing is reclaimed on stale evidence.
        self._activity: dict[str, float] = {}
        self._operations: dict[str, int] = {}
        # Last renewal per lease, same lifetime as _activity. The agent heartbeats only while its turn is
        # in flight, so a fresh renewal means the run is mid-turn — silent because it is thinking or
        # reading, not because it died — and gets a longer idle floor before it may be reclaimed.
        self._renewed: dict[str, float] = {}
        self._activity_lock = threading.Lock()

    def _operation_started(self, lease_id: str) -> None:
        with self._activity_lock:
            self._operations[lease_id] = self._operations.get(lease_id, 0) + 1
            self._activity[lease_id] = time.monotonic()

    def _operation_finished(self, lease_id: str) -> None:
        with self._activity_lock:
            left = self._operations.get(lease_id, 1) - 1
            if left > 0:
                self._operations[lease_id] = left
            else:
                self._operations.pop(lease_id, None)
            if lease_id in self._activity:  # never seed bookkeeping for an unknown lease id
                self._activity[lease_id] = time.monotonic()

    def touch(self, lease_id: str) -> None:
        with self._activity_lock:
            self._activity[lease_id] = time.monotonic()

    def idle_seconds(self, lease: dict) -> float:
        """Seconds since the last operation on this lease; zero while one is running."""
        with self._activity_lock:
            if self._operations.get(lease["lease_id"]):
                return 0.0
            return time.monotonic() - self._activity.setdefault(lease["lease_id"], time.monotonic())

    def _notify(self) -> None:
        with self._condition:
            self._generation += 1
            self._condition.notify_all()

    def is_ready(self) -> bool:
        return self._started and not self._fenced and not self._recovering and self.store.owns(self._token)

    def _stripe(self, lease_id: str):
        return self._stripes[hash(lease_id) % len(self._stripes)]

    @staticmethod
    def _id(handle: str | LeaseHandle) -> str:
        return handle.lease_id if isinstance(handle, LeaseHandle) else handle

    def _assert_owner(self) -> None:
        if self._fenced or not self.store.owns(self._token):
            raise BrokerOwnershipError("broker ownership lost; reconcile before serving requests")

    def _ensure_started(self) -> None:
        if not self._started:
            self.recover()
        self._assert_owner()

    def _provider_call(self, operation: str, *args, **kwargs):
        self._assert_owner()
        try:
            with span("sandbox.provider", provider=self.provider.name, operation=operation):
                return getattr(self.provider, operation)(*args, **kwargs)
        except Exception:
            metrics.SANDBOX_PROVIDER_ERRORS.labels(self.provider.name, operation).inc()
            raise

    def _save(self, lease: dict) -> None:
        self._assert_owner()
        version = lease.get("version")
        if not self.store.cas(lease["lease_id"], version, lease):
            raise LeaseConflictError("lease changed concurrently")
        lease["version"] = (version or 0) + 1

    def _load(self, handle: str | LeaseHandle, allow_expired: bool = False) -> dict:
        self._ensure_started()
        if self._recovering:
            raise BrokerOwnershipError("broker is rebuilding lease ownership")
        lease_id = self._id(handle)
        lease = self.store.get(lease_id)
        if not lease:
            raise LeaseLostError(f"lease not found: {lease_id}")
        if allow_expired:
            return lease
        if lease["state"] != "active":
            raise LeaseLostError(f"lease is {lease['state']}: {lease_id}")
        if _expired(lease):
            raise LeaseExpiredError(f"lease expired: {lease_id}")
        if isinstance(handle, LeaseHandle) and handle.provider_ref != lease["provider_ref"]:
            raise LeaseLostError("lease handle does not match its resource")
        try:
            resource = self._provider_call("inspect", lease["provider_ref"])
        except SandboxError as exc:
            raise LeaseError("sandbox inspection unavailable") from exc
        if resource and (resource.labels.get("ailab.owner") != self.owner or resource.labels.get("ailab.lease_id") != lease_id):
            raise LeaseLostError("sandbox identity does not match lease")
        if resource is None or not resource.running:
            with self._stripe(lease_id):
                current = self.store.get(lease_id)
                if current and current["state"] == "active":
                    if resource is None:
                        self._finish(current, "failed", "lease_lost")
                    else:
                        self.release(lease_id, reason="lease_lost")
            raise LeaseLostError(f"sandbox disappeared: {lease_id}")
        return lease

    def active_leases(self) -> list[dict]:
        # Creating and releasing resources occupy capacity until deletion is confirmed.
        return self.store.live()

    def status(self, handle: str | LeaseHandle) -> dict:
        self._ensure_started()
        if self._recovering:
            raise BrokerOwnershipError("broker is rebuilding lease ownership")
        lease = self.store.get(self._id(handle))
        if lease is None:
            raise LeaseLostError("lease not found")
        return lease

    def admission_status(self, run_id: str) -> dict:
        with self._condition:
            return self.admission.status(run_id)

    def put_input(self, lease: dict, path: str, data: bytes) -> None:
        """Inject a platform file while a lease is being prepared (inside acquire's prepare callback).

        Unlike put_file this is for trusted platform inputs, so it is not bound by the agent's write cap.
        """
        self._provider_call("put_file", lease["provider_ref"], self._checked(path), data)

    @contextmanager
    def transfer_file(self):
        # /data is disk-backed in both deployment overlays. /tmp may be tmpfs,
        # which would merely move the same OOM risk out of the Python heap.
        root = self._transfer_root
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="transfer-", dir=root) as directory:
            yield Path(directory) / "file"

    def inject_file(self, lease: dict, file_id: str, path: str, *, max_bytes: int | None = None, **binding) -> None:
        limit = min(max_bytes or settings.max_upload_bytes, settings.max_upload_bytes)
        with self.transfer_file() as local:
            self.platform.download_file(file_id, local, max_bytes=limit, **binding)
            self._provider_call("put_file_from_path", lease["provider_ref"], self._checked(path), local, max_bytes=limit)

    def _download(self, lease: dict, path: str, local: Path, limit: int) -> None:
        size = self._provider_call("file_size", lease["sandbox_id"], path)
        if size > limit:
            raise TransferLimitError(f"file is {size} bytes; the limit is {limit}")
        self._provider_call("get_file_to_path", lease["sandbox_id"], path, local, max_bytes=limit)

    def store_file(self, lease: dict, path: str, key: str, content_type: str, limit: int) -> tuple[int, str]:
        with self.transfer_file() as local:
            self._download(lease, path, local, limit)
            digest = hashlib.sha256()
            with local.open("rb") as stream:
                for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
                    digest.update(chunk)
                stream.seek(0)
                self.storage.put_stream(key, stream, content_type, max_bytes=limit)
            return local.stat().st_size, digest.hexdigest()

    @staticmethod
    def default_spec(image: str | None = None, ttl_seconds: int | None = None) -> SandboxSpec:
        return SandboxSpec(
            image=image or settings.sandbox_image,
            cpu=settings.sandbox_cpu,
            memory=settings.sandbox_memory_bytes,
            disk=settings.sandbox_disk_bytes,
            ttl=ttl_seconds if ttl_seconds is not None else settings.sandbox_ttl_seconds,
            max_lifetime=settings.sandbox_max_lifetime_seconds,
        )

    @staticmethod
    def _metadata(lease: dict) -> dict[str, str]:
        labels = {
            f"ailab.{key}": str(lease[key])
            for key in ("owner", "lease_id", "run_id", "user_id", "created_at", "expires_at", "max_expires_at", "state")
        }
        labels["ailab.spec"] = json.dumps(lease["spec"], separators=(",", ":"))
        # Only recovery-critical fields; preview caches stay ephemeral in Redis.
        labels["ailab.inputs"] = json.dumps(lease.get("inputs", []), ensure_ascii=False)
        labels["ailab.preview_seq"] = str(lease.get("preview_seq", 0))
        return labels

    def acquire(
        self,
        spec: SandboxSpec,
        principal: LeasePrincipal,
        *,
        wait_seconds: float | None = None,
        on_queued: Callable[[AdmissionDecision], None] | None = None,
        prepare: Callable[[dict], None] | None = None,
        ensure_active: Callable[[], None] | None = None,
    ) -> LeaseHandle:
        """Trusted service API. prepare runs before activation; C can omit it for render jobs.

        Queue entries are in-process, bounded and deadline-driven. If the Broker
        restarts a waiting caller must retry; no sandbox has been reserved yet.
        """
        self._ensure_started()
        if spec.max_lifetime > settings.sandbox_max_lifetime_seconds:
            raise AdmissionRejected("spec_exceeds_max_lifetime")
        self._release_expired()
        started = time.perf_counter()
        wait = settings.sandbox_wait_seconds if wait_seconds is None else max(0, wait_seconds)
        deadline, request_id = time.monotonic() + wait, uuid.uuid4().hex
        last_position, next_reclaim = None, 0.0
        with self._condition:
            if principal.run_id in self._pending_runs:
                raise AdmissionRejected("run_already_waiting")
            self._pending_runs.add(principal.run_id)
        try:
            while True:
                if ensure_active:
                    ensure_active()
                with self._condition:
                    self._assert_owner()
                    if self._recovering:
                        raise BrokerOwnershipError("broker is reconciling after store loss")
                    generation = self._generation
                    existing = next((x for x in self.active_leases() if x["run_id"] == principal.run_id), None)
                    if existing:
                        if existing["user_id"] != principal.user_id:
                            raise LeaseError("run principal mismatch")
                        if existing["state"] != "active" or _expired(existing):
                            raise AdmissionRejected("run_lease_not_active")
                        return LeaseHandle(existing["lease_id"], existing["provider_ref"])
                    decision = self.admission.admit(
                        principal, spec, request_id=request_id, wait_seconds=max(0, deadline - time.monotonic())
                    )
                    if decision.status == "rejected":
                        metrics.SANDBOX_CAPACITY_REJECTIONS.inc()
                        if decision.reason == "queue_deadline":
                            raise AdmissionTimeout("queue_deadline")
                        raise AdmissionRejected(decision.reason or "capacity")
                    if decision.status == "admitted":
                        now = datetime.now(UTC)
                        lease_id = f"lease_{uuid.uuid4().hex}"
                        lease = {
                            "lease_id": lease_id,
                            "owner": self.owner,
                            "run_id": principal.run_id,
                            "user_id": principal.user_id,
                            "provider": self.provider.name,
                            "provider_ref": None,
                            "sandbox_id": None,
                            "image": spec.image,
                            "spec": asdict(spec),
                            "state": "creating",
                            "created_at": now.isoformat(),
                            "expires_at": (now + timedelta(seconds=spec.ttl)).isoformat(),
                            "max_expires_at": (now + timedelta(seconds=spec.max_lifetime)).isoformat(),
                            "create_deadline": (now + timedelta(seconds=settings.osb_request_timeout_seconds + 150)).isoformat(),
                        }
                        # Registered in flight before the first save: cleanup_once skips
                        # creating-state leases it finds in this set, and the save is the
                        # moment the lease first becomes visible to it.
                        self._inflight.add(lease_id)
                        self._save(lease)
                        break
                    position, blocked_by = decision.position, decision.reason
                # Event delivery never holds the admission condition across an HTTP call.
                if on_queued and position != last_position:
                    on_queued(decision)
                last_position = position
                if blocked_by in {"user_limit", "capacity"} and time.monotonic() >= next_reclaim:
                    next_reclaim = time.monotonic() + EVICT_RETRY_SECONDS
                    self._reclaim_for(principal, spec, blocked_by)
                with self._condition:
                    # Do not miss a release while the progress callback runs.
                    remaining = deadline - time.monotonic()
                    if remaining > 0 and generation == self._generation:
                        self._condition.wait(min(remaining, 1.0) if ensure_active else remaining)
        finally:
            with self._condition:
                self._pending_runs.discard(principal.run_id)
                self.admission.cancel(request_id)
                self._notify()
            metrics.SANDBOX_ADMISSION_WAIT_SECONDS.observe(time.perf_counter() - started)

        create_started = False
        try:
            with self._stripe(lease_id):
                if ensure_active:
                    ensure_active()
                create_started = True
                ref = self._provider_call("acquire", lease_id, spec.image, ttl_seconds=spec.ttl, spec=spec, labels=self._metadata(lease))
                self._assert_owner()
                lease.update(provider_ref=ref, sandbox_id=ref)
                self._save(lease)
                if prepare:
                    prepare(lease)
                if ensure_active:
                    ensure_active()
                self._provider_call("renew", ref, datetime.fromisoformat(lease["expires_at"]))
                if _expired(lease):
                    raise LeaseExpiredError("lease expired during sandbox preparation")
                lease["state"] = "active"
                self.touch(lease_id)
                # Activation reaches the resource first, so a crash/Redis flush can
                # only adopt fully staged input, never a partially prepared sandbox.
                self._provider_call("update_metadata", ref, self._metadata(lease))
                self._save(lease)
        except BaseException as exc:
            if not isinstance(exc, BrokerOwnershipError):
                if create_started:
                    self.release(lease_id, reason="creation_failed")
                else:
                    self._finish(lease, "failed", "canceled_before_creation")
            if isinstance(exc, LeaseError) or not isinstance(exc, Exception):
                raise
            raise LeaseError("could not acquire or prepare sandbox") from exc
        finally:
            with self._condition:
                self._inflight.discard(lease_id)
                self._notify()
        metrics.SANDBOX_ACQUIRE_SECONDS.observe(time.perf_counter() - started)
        return LeaseHandle(lease_id, ref)

    def create(
        self,
        run_id: str,
        image: str | None = None,
        ttl_seconds: int | None = None,
        *,
        wait_seconds: float = 0,
    ) -> dict:
        # Ownership and attachments come only from the API, never the Agent's body.
        run = self.platform.get_run(run_id)
        user_id = run.get("user_id")
        if not user_id:
            raise LeaseError("API run has no authenticated owner")

        def ensure_active() -> None:
            current = (getattr(self.platform, "get_run_state", None) or self.platform.get_run)(run_id)
            if current.get("status") not in {None, "queued", "running"}:
                raise LeaseCanceledError("任务已结束或已取消")

        def prepare(lease: dict) -> None:
            injected, used = [], set()
            for file_id in run.get("input_file_ids") or []:
                meta = self.platform.get_file(file_id, run_id=run_id)
                if meta.get("status") != "ready":
                    raise LeaseError(f"input file {file_id} is {meta.get('status')}")
                name = _unique_name(_safe_name(meta.get("filename") or file_id), used)
                path = f"/workspace/inputs/{name}"
                self.inject_file(lease, file_id, path, run_id=run_id)
                injected.append({"file_id": file_id, "filename": name, "path": path})
            lease["inputs"] = injected
            if run.get("workspace_restore_key"):
                self._restore_workspace(lease, run["workspace_restore_key"])
            # Restore first: a previous workspace must never override this run's frozen brand.
            if "brand" in run:
                result = self._provider_call("exec", lease["provider_ref"], "rm -rf /workspace/.brand", 10)
                if result.exit_code:
                    raise LeaseError("could not reset brand directory")
            result = self._provider_call("exec", lease["provider_ref"], "rm -rf /workspace/.template", 10)
            if result.exit_code:
                raise LeaseError("could not reset template directory")
            template = (run.get("options") or {}).get("template")
            if template and (run.get("options") or {}).get("mode", "work") == "work":
                file_id = template["file_id"]
                meta = self.platform.get_file(file_id, run_id=run_id, job="template")
                if meta.get("status") != "ready" or meta["size"] > settings.max_template_bytes:
                    raise LeaseError("template injection limit exceeded")
                self.inject_file(
                    lease,
                    file_id,
                    "/workspace/.template/template.pptx",
                    run_id=run_id,
                    job="template",
                    max_bytes=settings.max_template_bytes,
                )
                analysis = (meta.get("meta") or {}).get("template") or (meta.get("meta") or {}).get("brand")
                if analysis:
                    with self.transfer_file() as local:
                        local.write_text(json.dumps(analysis, ensure_ascii=False))
                        self._provider_call(
                            "put_file_from_path", lease["provider_ref"], "/workspace/.template/analysis.json", local, max_bytes=256 * 1024
                        )
            brand = run.get("brand") or {}
            if brand and (run.get("options") or {}).get("mode", "work") == "work":
                names = {"logo": "logo.png", "pptx": "template.pptx", "docx": "template.docx"}
                total = 0
                for kind, file_id in (brand.get("assets") or {}).items():
                    if kind not in names:
                        raise LeaseError("unknown brand asset kind")
                    meta = self.platform.get_file(file_id, run_id=run_id, job="brand")
                    limit = 2 * 1024 * 1024 if kind == "logo" else 10 * 1024 * 1024
                    size = (meta.get("meta") or {}).get("brand", {}).get("png_bytes", meta["size"]) if kind == "logo" else meta["size"]
                    total += size
                    if meta.get("status") != "ready" or size > limit or total > 22 * 1024 * 1024:
                        raise LeaseError("brand injection limit exceeded")
                    self.inject_file(lease, file_id, f"/workspace/.brand/{names[kind]}", run_id=run_id, job="brand", max_bytes=limit)
                with self.transfer_file() as local:
                    local.write_text(json.dumps(brand, ensure_ascii=False))
                    self._provider_call(
                        "put_file_from_path", lease["provider_ref"], "/workspace/.brand/brand.json", local, max_bytes=64 * 1024
                    )

        def queued(decision: AdmissionDecision) -> None:
            try:
                self.platform.append_event(
                    run_id, "agent.waiting_sandbox", {"position": decision.position, "request_id": decision.request_id}
                )
            except Exception:
                log.warning("could not record sandbox admission progress")

        handle = self.acquire(
            self.default_spec(image, ttl_seconds),
            LeasePrincipal(user_id, run_id),
            wait_seconds=wait_seconds,
            on_queued=queued,
            prepare=prepare,
            ensure_active=ensure_active,
        )
        lease = self.get(handle)
        try:
            self.platform.append_event(run_id, "sandbox.leased", {"lease_id": handle.lease_id, "provider": self.provider.name})
        except Exception:
            log.warning("could not record sandbox.leased")
        return lease

    def get(self, handle: str | LeaseHandle) -> dict:
        return self._load(handle)

    def renew(self, handle: str | LeaseHandle, ttl_seconds: int | None = None) -> dict:
        lease_id = self._id(handle)
        with self._stripe(lease_id):
            lease = self._load(handle)
            ttl = ttl_seconds if ttl_seconds is not None else lease["spec"]["ttl"]
            if ttl <= 0 or ttl > lease["spec"]["ttl"]:
                raise LeaseError("heartbeat ttl must be within the original lease ttl")
            until = min(datetime.now(UTC) + timedelta(seconds=ttl), datetime.fromisoformat(lease["max_expires_at"]))
            self._provider_call("renew", lease["provider_ref"], until)
            lease["expires_at"] = until.isoformat()
            self._provider_call("update_metadata", lease["provider_ref"], self._metadata(lease))
            self._save(lease)
            with self._activity_lock:
                self._renewed[lease_id] = time.monotonic()
            return lease

    @_operation
    def cancel_exec(self, handle: str | LeaseHandle) -> bool:
        lease = self._load(handle)
        return self._provider_call("cancel_exec", lease["provider_ref"])

    # ---------------------------------------------------------------- workspace across turns
    def _restore_workspace(self, lease: dict, key: str) -> None:
        """Unpack the previous turn's workspace. A failure costs the continuity, not the turn."""
        sandbox_id = lease["sandbox_id"]
        try:
            with self.transfer_file() as local:
                with self.storage.open_stream(key) as chunks, local.open("wb") as stream:
                    copy_chunks(chunks, stream, max_bytes=settings.max_workspace_bytes)
                self._provider_call("put_file_from_path", sandbox_id, SNAPSHOT_PATH, local, max_bytes=settings.max_workspace_bytes)
            res = self._provider_call("exec", sandbox_id, f"tar -xzf {SNAPSHOT_PATH} -C /workspace && rm -f {SNAPSHOT_PATH}", 120)
            if res.exit_code != 0:
                raise SandboxError(res.stderr[-300:] or f"tar exit {res.exit_code}")
            lease["restored"] = True
            # what is already there was previewed in an earlier turn: render only what changes from now on
            lease["preview_fp"] = self._fingerprints(sandbox_id)
        except Exception as exc:
            log.warning("workspace restore failed for %s from %s", lease["lease_id"], key, exc_info=True)
            lease["restored"] = False
            lease["restore_error"] = str(exc)[:300]

    @_operation
    def snapshot(self, lease_id: str) -> dict:
        """Pack /workspace (minus inputs and platform files) into storage for the conversation's next turn."""
        return self._snapshot_lease(self._load(lease_id))

    def _snapshot_lease(self, lease: dict) -> dict:
        sandbox_id = lease["sandbox_id"]
        cmd = (
            f"mkdir -p {TOOL_DIR} && tar -czf {SNAPSHOT_PATH} -C /workspace --exclude=./.ailab --exclude=./inputs . "
            f"&& stat -c %s {SNAPSHOT_PATH}"
        )
        res = self._provider_call("exec", sandbox_id, cmd, 180)
        size_line = (res.stdout.strip().splitlines() or [""])[-1]
        if res.exit_code != 0 or not size_line.isdigit():
            raise SandboxError(f"workspace snapshot failed: {res.stderr[-300:]}")
        size = int(size_line)
        key = f"workspaces/{lease['run_id']}/{uuid.uuid4().hex}.tgz"
        try:
            size, _ = self.store_file(lease, SNAPSHOT_PATH, key, "application/gzip", settings.max_workspace_bytes)
        except TransferLimitError as exc:
            raise ArtifactRejected(str(exc)) from exc
        finally:
            self._provider_call("exec", sandbox_id, f"rm -f {SNAPSHOT_PATH}", 30)
        return {"key": key, "size": size}

    # ---------------------------------------------------------------- live previews
    def _fingerprints(self, sandbox_id: str) -> dict[str, str]:
        names = " -o ".join(f"-iname '{p}'" for p in PREVIEW_PATTERNS)
        res = self._provider_call("exec", sandbox_id, f"find {OUTPUTS} -maxdepth 1 -type f \\( {names} \\) -printf '%p\\t%s:%T@\\n'", 30)
        out: dict[str, str] = {}
        for line in res.stdout.splitlines():
            path, _, fp = line.partition("\t")
            if path.startswith(OUTPUTS + "/") and fp:
                out[path] = fp
        return out

    @_operation
    def refresh_previews(self, lease_id: str) -> list[dict]:
        """Render previews of office files in /workspace/outputs that are new or changed since the last call.

        Pages become JPEGs and spreadsheets a JSON of cell values, stored under previews/<run>/<n>/ where the
        API serves them to the run's owner. Returns one manifest per rendered file (or one with an "error").
        """
        lease = self._load(lease_id)
        sandbox_id, run_id = lease["sandbox_id"], lease["run_id"]
        current = self._fingerprints(sandbox_id)
        seen: dict[str, str] = lease.get("preview_fp") or {}
        changed = [p for p, fp in sorted(current.items()) if seen.get(p) != fp][:MAX_PREVIEWS_PER_CALL]
        if not changed:
            return []
        if not lease.get("preview_tool"):
            self._provider_call("put_file", sandbox_id, PREVIEW_SCRIPT, RENDER_PREVIEW.read_bytes())
            lease["preview_tool"] = True
        manifests = []
        for path in changed:
            seq = int(lease.get("preview_seq", 0)) + 1
            lease["preview_seq"] = seq
            seen[path] = current[path]  # even when rendering fails: do not retry the same bytes forever
            outdir = f"{TOOL_DIR}/preview/{seq}"
            filename = Path(path).name
            started = time.perf_counter()
            res = self._provider_call("exec", sandbox_id, f"python {PREVIEW_SCRIPT} {shlex.quote(path)} {outdir} --pdf", 300)
            try:
                result = json.loads((res.stdout.strip().splitlines() or ["{}"])[-1])
            except json.JSONDecodeError:
                result = {"error": (res.stderr or res.stdout)[-300:] or f"renderer exit {res.exit_code}"}
            manifest = {"filename": filename, "path": path, "version": seq}
            try:
                if result.get("error"):
                    manifest["error"] = str(result["error"])[:300]
                elif result.get("kind") == "pages":
                    page_names = result.get("pages", [])
                    if (
                        not isinstance(page_names, list)
                        or not 1 <= len(page_names) <= 40
                        or any(not isinstance(name, str) or not re.fullmatch(r"p-\d+\.jpg", name) for name in page_names)
                    ):
                        raise SandboxError("invalid preview output")
                    names = []
                    total = 0
                    for name in page_names:
                        size, _ = self.store_file(
                            lease,
                            f"{outdir}/{name}",
                            f"previews/{run_id}/{seq}/{name}",
                            "image/jpeg",
                            min(settings.max_sandbox_read_bytes, 32 * 1024 * 1024 - total),
                        )
                        total += size
                        names.append(f"{seq}/{name}")
                    manifest.update(kind="pages", pages=names)
                elif result.get("kind") == "sheets":
                    self.store_file(
                        lease,
                        f"{outdir}/sheets.json",
                        f"previews/{run_id}/{seq}/sheets.json",
                        "application/json",
                        settings.max_sandbox_read_bytes,
                    )
                    manifest.update(kind="sheets", data=f"{seq}/sheets.json", errors=result.get("errors", []))
                elif result.get("kind") == "pdf" and result.get("pdf") == "document.pdf":
                    size, _ = self.store_file(
                        lease,
                        f"{outdir}/document.pdf",
                        f"previews/{run_id}/{seq}/document.pdf",
                        "application/pdf",
                        min(settings.max_sandbox_read_bytes, 32 * 1024 * 1024),
                    )
                    manifest.update(kind="pdf", pdf=f"{seq}/document.pdf", page_count=result.get("page_count"))
                else:
                    manifest["error"] = "renderer returned nothing usable"
            except Exception as exc:
                log.warning("storing preview %s of %s failed", seq, path, exc_info=True)
                manifest["error"] = f"could not store the preview: {exc}"[:300]
            manifest["seconds"] = round(time.perf_counter() - started, 1)
            self._provider_call("exec", sandbox_id, f"rm -rf {outdir}", 30)
            manifests.append(manifest)
        lease["preview_fp"] = seen
        with self._stripe(lease_id):
            # Rendering does not block heartbeats. Merge only preview fields into
            # the latest lease version, preserving renew/release decisions.
            latest = self._load(lease_id)
            latest.update({key: lease[key] for key in ("preview_fp", "preview_seq", "preview_tool") if key in lease})
            self._provider_call("update_metadata", latest["provider_ref"], self._metadata(latest))
            self._save(latest)
        return manifests

    def _finish(self, lease: dict, state: str, reason: str) -> None:
        lease.update(state=state, reason=reason, closed_at=datetime.now(UTC).isoformat())
        self._save(lease)
        with self._activity_lock:
            self._activity.pop(lease["lease_id"], None)
            self._renewed.pop(lease["lease_id"], None)
            self._operations.pop(lease["lease_id"], None)
        with self._condition:
            self._notify()

    def release(self, handle: str | LeaseHandle, reason: str = "released") -> None:
        self._ensure_started()
        lease_id = self._id(handle)
        with self._stripe(lease_id):
            lease = self.store.get(lease_id)
            if not lease and self._recovering:
                raise BrokerOwnershipError("broker is rebuilding lease ownership")
            if not lease or lease["state"] in {"released", "failed"}:
                return
            if self._being_saved(lease):
                return  # the reclaim in progress deletes it once the workspace is stored
            if isinstance(handle, LeaseHandle) and lease["provider_ref"] != handle.provider_ref:
                raise LeaseLostError("lease handle does not match its resource")
            if lease["state"] != "releasing":
                lease.update(state="releasing", reason=reason, retry_at=0, release_attempts=0)
                self._save(lease)
            self._delete_resource(lease)

    def _delete_resource(self, lease: dict) -> None:
        self._assert_owner()
        ref = lease.get("provider_ref")
        if not ref:
            # A timed-out create may still finish on the provider. Keep the intent
            # until list_owned finds it or the create deadline has elapsed.
            if datetime.fromisoformat(lease["create_deadline"]) > datetime.now(UTC):
                return
            self._finish(lease, "failed", "creation_failed")
            return
        try:
            resource = self._provider_call("inspect", ref)
            if resource is not None:
                try:
                    self._provider_call("update_metadata", ref, self._metadata(lease))
                except Exception:
                    log.warning("could not persist releasing intent on resource")
                self._provider_call("release", ref)
                if self._provider_call("inspect", ref) is not None:
                    raise SandboxError("provider has not confirmed deletion")
        except Exception:
            attempts = int(lease.get("release_attempts", 0)) + 1
            lease.update(
                release_attempts=attempts, retry_at=time.time() + min(60, settings.broker_release_retry_seconds * 2 ** min(attempts - 1, 6))
            )
            self._save(lease)
            metrics.SANDBOX_RECONCILE_ACTIONS.labels("delete_retry").inc()
            return
        self._finish(lease, "released", lease.get("reason", "released"))
        metrics.SANDBOX_RECONCILE_ACTIONS.labels("released").inc()
        try:
            self.platform.append_event(
                lease["run_id"], "sandbox.released", {"lease_id": lease["lease_id"], "reason": lease.get("reason", "released")}
            )
        except Exception as exc:
            log.warning("sandbox.released event not appended for run %s: %s", lease["run_id"], exc)

    # ---------------------------------------------------------------- sandbox I/O
    @_operation
    def exec(self, lease_id: str, cmd: str, timeout: int | None = None) -> ExecResult:
        lease = self._load(lease_id)
        timeout = timeout or settings.sandbox_exec_timeout_seconds
        with metrics.SANDBOX_EXEC_SECONDS.labels(self.provider.name).time():
            try:
                result = self._provider_call("exec", lease["sandbox_id"], cmd, timeout)
                if result.exit_code != 0:
                    # A killed container may return an exit status instead of raising.
                    self._load(lease_id)
                return result
            except SandboxError:
                # A resource can vanish after the preflight inspection.
                self._load(lease_id)
                raise

    @staticmethod
    def _checked(path: str, under: str = "/workspace") -> str:
        try:
            return normalize_path(path, under)
        except PathError as exc:
            raise SandboxError(str(exc)) from exc

    @_operation
    def put_file(self, lease_id: str, path: str, data: bytes) -> None:
        lease = self._load(lease_id)
        path = self._checked(path)
        if len(data) > settings.max_sandbox_write_bytes:
            raise SandboxError(f"file too large to write ({len(data)} > {settings.max_sandbox_write_bytes} bytes)")
        self._provider_call("put_file", lease["sandbox_id"], path, data)

    @_operation
    def put_fetched(self, lease_id: str, path: str, local: Path, size: int) -> None:
        """A public download the Agent fetched outside the sandbox, already spooled to disk by the route."""
        lease = self._load(lease_id)
        path = self._checked(path)
        if size > settings.web_download_max_bytes:
            raise SandboxError(f"download too large ({size} > {settings.web_download_max_bytes} bytes)")
        self._provider_call("put_file_from_path", lease["sandbox_id"], path, local, max_bytes=settings.web_download_max_bytes)

    @_operation
    def get_file(self, lease_id: str, path: str) -> bytes:
        lease = self._load(lease_id)
        path = self._checked(path)
        with self.transfer_file() as local:
            self._download(lease, path, local, settings.max_sandbox_read_bytes)
            return local.read_bytes()

    @_operation
    def list_files(self, lease_id: str, path: str) -> list[dict]:
        lease = self._load(lease_id)
        return self._provider_call("list_files", lease["sandbox_id"], self._checked(path))

    @_operation
    def commit(self, lease_id: str, path: str, filename: str | None = None, role: str = "output") -> dict:
        lease = self._load(lease_id)
        path = self._checked(path, OUTPUTS)  # only /workspace/outputs is archived
        if path == OUTPUTS:
            raise SandboxError("deliver a file inside /workspace/outputs, not the directory")
        size = self._provider_call("file_size", lease["sandbox_id"], path)
        if size > settings.max_artifact_bytes:
            raise ArtifactRejected(f"{path} is {size} bytes; the limit is {settings.max_artifact_bytes}")
        name = _safe_name(filename or Path(path).name)
        staging_key = f"staging/{lease_id}/{uuid.uuid4().hex}/{name}"
        try:
            size, sha256 = self.store_file(
                lease, path, staging_key, mimetypes.guess_type(name)[0] or "application/octet-stream", settings.max_artifact_bytes
            )
        except TransferLimitError as exc:
            raise ArtifactRejected(str(exc)) from exc
        payload = {
            "run_id": lease["run_id"],
            "staging_key": staging_key,
            "filename": name,
            "content_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "size": size,
            "sha256": sha256,
            "role": role,
        }
        try:
            delivered = self.platform.commit_artifact(payload)
            metrics.ARTIFACT_BYTES.inc(size)
            return delivered
        except Exception:
            log.exception("artifact commit failed for lease %s staging=%s", lease_id, staging_key)
            # A retry stages a fresh object under a new key, so this one would leak.
            try:
                self.storage.delete(staging_key)
            except Exception:
                log.warning("could not delete staging object %s", staging_key, exc_info=True)
            raise

    # ---------------------------------------------------------------- reclaiming idle sandboxes
    @staticmethod
    def _being_saved(lease: dict) -> bool:
        return lease.get("state") == "releasing" and lease.get("reason") == EVICT_REASON and lease.get("retry_at", 0) > time.time()

    def _pick_victim(self, principal: LeasePrincipal, why: str, live: list[dict]) -> dict | None:
        """The sandbox to give up for a waiting request, or None when nobody may be asked to.

        A sandbox is only ever reclaimed after sandbox_evict_idle_seconds without any operation, so two
        busy runs cannot trade places forever. A lease whose agent is still heartbeating is mid-turn —
        silent because it is thinking or reading, not dead — and gets a 3x floor before it may be asked;
        a heartbeat that stops (crashed agent, cut connection) leaves it reclaimable on the base floor.
        Under the user's own cap only their own sandboxes qualify; for pool capacity everyone's do,
        but only once the pool is almost full, and the user holding the most sandboxes gives one up first.
        """
        work = [x for x in live if x["spec"]["profile"] != "render"]
        mine = why == "user_limit"
        scope = [x for x in work if x["user_id"] == principal.user_id] if mine else work
        if any(x["state"] == "releasing" and x.get("reason") == EVICT_REASON for x in scope):
            return None  # the capacity from the last reclaim is still on its way
        if why == "capacity" and self.admission.utilization(live) < settings.sandbox_preempt_utilization:
            return None
        held = Counter(x["user_id"] for x in work)
        idle = {x["lease_id"]: self.idle_seconds(x) for x in scope}
        candidates = [
            x
            for x in scope
            if x["state"] == "active"
            and x["run_id"] != principal.run_id
            and not _expired(x)
            and idle[x["lease_id"]] >= self._evict_floor(x["lease_id"])
        ]
        return max(candidates, key=lambda x: (held[x["user_id"]], idle[x["lease_id"]]), default=None)

    def _renew_fresh(self, lease_id: str) -> bool:
        with self._activity_lock:
            last = self._renewed.get(lease_id)
        return last is not None and (time.monotonic() - last) <= 3 * settings.runtime_heartbeat_seconds

    def _evict_floor(self, lease_id: str) -> float:
        return settings.sandbox_evict_idle_seconds * (3 if self._renew_fresh(lease_id) else 1)

    def _reclaim_for(self, principal: LeasePrincipal, spec: SandboxSpec, why: str) -> bool:
        """Start giving up one idle sandbox. Never called with the admission condition held."""
        if spec.profile == "render":
            return False
        try:
            victim = self._pick_victim(principal, why, self.active_leases())
            if victim is None:
                return False
            lease_id = victim["lease_id"]
            with self._stripe(lease_id):
                lease = self.store.get(lease_id)
                if not lease or lease["state"] != "active" or self.idle_seconds(lease) < self._evict_floor(lease_id):
                    return False
                # releasing first: every call on it now answers 410, and reconciliation leaves it alone while it is saved
                lease.update(state="releasing", reason=EVICT_REASON, retry_at=time.time() + EVICT_SAVE_SECONDS)
                lease["evicted_for"] = principal.run_id
                self._save(lease)
        except (LeaseConflictError, BrokerOwnershipError):
            return False
        metrics.SANDBOX_EVICTIONS.labels(why).inc()
        log.info("reclaiming idle sandbox %s (run %s) for run %s: %s", lease_id, victim["run_id"], principal.run_id, why)
        threading.Thread(target=self._finish_eviction, args=(lease_id,), name=f"evict-{lease_id[:12]}", daemon=True).start()
        return True

    def _finish_eviction(self, lease_id: str) -> None:
        """Save the workspace, record where, then delete. A failed save still frees the slot: the run's own checkpoint remains."""
        try:
            deadline = time.monotonic() + EVICT_QUIET_SECONDS
            while self._operations.get(lease_id) and time.monotonic() < deadline:
                time.sleep(0.25)
            lease = self.store.get(lease_id)
            saved = None
            if lease and lease.get("provider_ref"):
                try:
                    saved = {**self._snapshot_lease(lease), "saved_at": time.time()}
                except Exception:
                    log.warning("workspace not saved before reclaiming %s", lease_id, exc_info=True)
            for _ in range(3):
                with self._stripe(lease_id):
                    lease = self.store.get(lease_id)
                    if not lease or lease["state"] != "releasing":
                        return
                    lease.update(retry_at=0, eviction_done=True)
                    if saved:
                        lease["eviction_snapshot"] = saved
                    try:
                        self._save(lease)
                    except LeaseConflictError:
                        continue
                    self._delete_resource(lease)
                    return
        except Exception:
            log.warning("reclaiming %s did not finish; reconciliation will delete it", lease_id, exc_info=True)

    # ---------------------------------------------------------------- housekeeping
    def recover(self) -> None:
        """Acquire single-Broker ownership, then reconcile desired and observed state."""
        with self._start_lock:
            if self._fenced or not self.store.claim_owner(self._token, settings.broker_lock_ttl_seconds):
                self._fenced = True
                raise BrokerOwnershipError("another live Broker owns this instance group")
            if not self._started:
                # Only the new fenced owner may reclaim spools left by a killed
                # predecessor. Never sweep another owner or live transfers.
                if self._transfer_root.exists():
                    shutil.rmtree(self._transfer_root)
            self._started = True
            # Owner heartbeat must run even while a slow provider is being reconciled.
            if self._start_background:
                self.start_cleanup_thread()
            self._recovering = True
            try:
                self.cleanup_once()
            except Exception:
                self._fenced = True
                raise
            finally:
                self._recovering = False

    def _from_resource(self, resource: SandboxResource) -> dict | None:
        labels = resource.labels
        try:
            spec = SandboxSpec(**json.loads(labels["ailab.spec"]))
            lease = {
                key: labels[f"ailab.{key}"]
                for key in ("owner", "lease_id", "run_id", "user_id", "created_at", "expires_at", "max_expires_at", "state")
            }
            if lease["owner"] != self.owner or not lease["lease_id"] or not lease["user_id"] or not lease["run_id"]:
                return None
            for key in ("created_at", "expires_at", "max_expires_at"):
                if datetime.fromisoformat(lease[key]).tzinfo is None:
                    return None
            if lease["state"] not in LIVE_STATES:
                return None
            lease.update(
                spec=asdict(spec),
                provider=self.provider.name,
                provider_ref=resource.provider_ref,
                sandbox_id=resource.provider_ref,
                image=spec.image,
                inputs=json.loads(labels.get("ailab.inputs", "[]")),
                create_deadline=lease["created_at"],
                preview_seq=int(labels.get("ailab.preview_seq", "0")),
            )
            return lease
        except (KeyError, ValueError, TypeError):
            return None

    def _release_expired(self) -> None:
        for lease in self.active_leases():
            if lease["state"] == "active" and _expired(lease):
                self.release(lease["lease_id"], reason="expired")

    def cleanup_once(self) -> None:
        self._assert_owner()
        seen_ids: set[str] = set()
        with self._reconcile_lock:
            resources = self._provider_call("list_owned", self.owner)
            by_id = {r.provider_ref: r for r in resources}
            # Redis is disposable: fully labelled active resources rebuild desired state.
            # Incomplete/expired resources are orphans; never stage them a second time.
            for resource in resources:
                lease_id = resource.labels.get("ailab.lease_id", "")
                with self._stripe(lease_id):
                    lease = self.store.get(lease_id) if lease_id else None
                    if lease and lease.get("provider_ref") is None and lease["state"] in LIVE_STATES:
                        lease.update(provider_ref=resource.provider_ref, sandbox_id=resource.provider_ref)
                        self._save(lease)
                    elif lease is None:
                        rebuilt = self._from_resource(resource)
                        if rebuilt and rebuilt["state"] == "active" and resource.running and not _expired(rebuilt):
                            self._save(rebuilt)
                            metrics.SANDBOX_RECONCILE_ACTIONS.labels("rebuilt").inc()
                            continue
                        # Assign an identity even to an incompletely labelled orphan so
                        # failed deletion remains tracked and backed off across passes.
                        lease = rebuilt or {
                            "lease_id": f"orphan_{uuid.uuid5(uuid.NAMESPACE_URL, resource.provider_ref).hex}",
                            "owner": self.owner,
                            "run_id": "",
                            "user_id": "",
                            "provider": self.provider.name,
                            "provider_ref": resource.provider_ref,
                            "sandbox_id": resource.provider_ref,
                            "spec": asdict(self.default_spec()),
                            "created_at": datetime.now(UTC).isoformat(),
                            "expires_at": datetime.now(UTC).isoformat(),
                            "create_deadline": datetime.now(UTC).isoformat(),
                            "max_expires_at": datetime.now(UTC).isoformat(),
                        }
                        lease.update(state="releasing", reason="orphan", retry_at=0)
                        if self.store.get(lease["lease_id"]) is None:
                            self._save(lease)
                            metrics.SANDBOX_RECONCILE_ACTIONS.labels("orphan").inc()
                    elif lease.get("provider_ref") != resource.provider_ref or lease["state"] in {"released", "failed"}:
                        # Duplicate resource / terminal intent: never adopt a new mapping.
                        orphan_id = f"orphan_{uuid.uuid5(uuid.NAMESPACE_URL, resource.provider_ref).hex}"
                        if self.store.get(orphan_id) is None:
                            orphan = {
                                **lease,
                                "lease_id": orphan_id,
                                "provider_ref": resource.provider_ref,
                                "sandbox_id": resource.provider_ref,
                                "state": "releasing",
                                "reason": "orphan",
                                "retry_at": 0,
                            }
                            orphan.pop("version", None)
                            self._save(orphan)
            for candidate in self.store.all():
                lease_id = candidate["lease_id"]
                seen_ids.add(lease_id)
                with self._stripe(lease_id):
                    lease = self.store.get(lease_id)
                    if not lease:
                        continue
                    if lease["state"] in {"released", "failed"}:
                        if time.time() - datetime.fromisoformat(lease["closed_at"]).timestamp() > settings.broker_record_retention_seconds:
                            self.store.delete(lease_id, lease["version"])
                        continue
                    resource = by_id.get(lease.get("provider_ref"))
                    if resource is None and lease.get("provider_ref") and lease["state"] in {"active", "creating"}:
                        # The list is a snapshot; allocation may finish after it was read.
                        resource = self._provider_call("inspect", lease["provider_ref"])
                    if lease["state"] == "creating" and lease_id in self._inflight:
                        continue
                    if lease["state"] == "active":
                        if lease["spec"].get("profile") != "render" and hasattr(self.platform, "get_run"):
                            try:
                                run = (getattr(self.platform, "get_run_state", None) or self.platform.get_run)(lease["run_id"])
                            except ClientError as exc:
                                if exc.status == 404:
                                    self.release(lease_id, reason="run_deleted")
                                    continue
                            except Exception:
                                log.warning("run state unavailable during reconciliation; retaining lease")
                            else:
                                if run.get("status") not in {None, "queued", "running"}:
                                    self.release(lease_id, reason="run_finished")
                                    continue
                        # A listing can observe startup before activation wins the
                        # stripe. Confirm stopped state against the current resource
                        # before deleting a lease that has since become active.
                        if resource is not None and not resource.running:
                            resource = self._provider_call("inspect", lease["provider_ref"])
                        if resource is None or not resource.running:
                            if resource is None:
                                self._finish(lease, "failed", "lease_lost")
                            else:
                                self.release(lease_id, reason="lease_lost")
                            metrics.SANDBOX_RECONCILE_ACTIONS.labels("lost").inc()
                        elif _expired(lease):
                            self.release(lease_id, reason="expired")
                        else:
                            metrics.SANDBOX_RECONCILE_ACTIONS.labels("adopted").inc()
                    elif lease["state"] == "creating":
                        # A restart interrupted creation/staging. Provider active metadata
                        # proves staging completed just before the final store CAS.
                        rebuilt = self._from_resource(resource) if resource else None
                        if rebuilt and rebuilt["state"] == "active" and resource.running and not _expired(rebuilt):
                            lease.update(rebuilt)
                            self._save(lease)
                        else:
                            self.release(lease_id, reason="creation_interrupted")
                    elif lease["state"] == "releasing" and lease.get("retry_at", 0) <= time.time():
                        self._delete_resource(lease)
            # Bookkeeping is per process and keyed by lease id; drop what no store row remembers any more
            # (call attempts on unknown ids would otherwise accumulate until restart).
            with self._activity_lock:
                for bookkeeping in (self._activity, self._renewed, self._operations):
                    for stale in [key for key in bookkeeping if key not in seen_ids]:
                        bookkeeping.pop(stale, None)
            with self._condition:
                self._notify()

    def start_cleanup_thread(self) -> threading.Thread:
        if self._threads:
            return self._threads[-1]

        def heartbeat() -> None:
            while not self._stop.wait(settings.broker_lock_ttl_seconds / 3):
                try:
                    # A Redis flush removes this lock too. Only one process may reclaim it;
                    # admission stays unavailable until provider reconciliation completes.
                    missing = not self.store.owns(self._token)
                    if missing:
                        self._recovering = True
                    if not self.store.claim_owner(self._token, settings.broker_lock_ttl_seconds):
                        raise BrokerOwnershipError("broker owner lock taken by another process")
                    if missing:
                        self.cleanup_once()
                        self._recovering = False
                        self._notify()
                except BrokerOwnershipError:
                    self._fenced = True
                    with self._condition:
                        self._notify()
                    log.error("broker fenced: the owner lock was taken by another process")
                    return
                except (RedisConnectionError, RedisTimeoutError) as exc:
                    # An unreachable Redis is not a lost lock. Nothing mutated while it was
                    # down - every mutation goes through the store - and once it answers
                    # again the claim above is authoritative: if the TTL lapsed and another
                    # broker took over, that claim fails and we fence then. Fencing here
                    # would trade one blip for a permanent stop only a pod restart cures.
                    log.warning("broker ownership heartbeat skipped: %s", exc)
                except Exception:
                    self._fenced = True
                    with self._condition:
                        self._notify()
                    log.error("broker fenced after ownership failure", exc_info=True)
                    return

        def reconcile() -> None:
            while not self._stop.wait(settings.broker_reconcile_interval_seconds):
                try:
                    self.cleanup_once()
                except BrokerOwnershipError:
                    if self._fenced:
                        return
                except Exception:
                    log.warning("lease reconciliation failed", exc_info=True)

        for name, target in (("broker-owner", heartbeat), ("lease-reconcile", reconcile)):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()
        return self._threads[-1]

    def close(self) -> None:
        """Stop housekeeping without destroying active resources; next Broker adopts them."""
        self._stop.set()
        with self._condition:
            self._fenced = True
            self._notify()
        for thread in self._threads:
            thread.join(timeout=1)
        if not any(thread.is_alive() for thread in self._threads):
            self.store.release_owner(self._token)
