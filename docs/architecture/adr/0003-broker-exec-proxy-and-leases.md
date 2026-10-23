# ADR 0003 · Broker exec proxy and recoverable single-node leases

Status: accepted for the single-node implementation (2026-10-23).

The Broker is the only sandbox lifecycle and exec boundary. The Agent receives an opaque lease id and calls the Broker; it never receives a container socket, OpenSandbox API key, execd address/token, or a sandbox id selected by model output. The target architecture's direct Agent → authenticated execd path requires verified Secure Access and network isolation. The single-node server does not yet establish that path, so proxying exec/file access keeps ownership, path checks and byte limits in one trusted service. This adds one internal HTTP hop and one provider inspection per operation (another inspection after a nonzero exec result to distinguish resource loss).

Per ADR 0001, sandboxes belong to one turn and are destroyed after that turn. The three-day workspace snapshot preserves files only. There is no idle reuse, idle LRU, warm pool, runtime checkpoint, or business-database sandbox session in this change.

## Ownership and reconciliation

`SandboxSpec(image, profile, cpu, memory, disk, ttl, max_lifetime)` and `LeaseHandle(lease_id, provider_ref)` live in `contracts/sandbox.py` and are re-exported from `sandbox/base.py`. Memory/disk use bytes, CPU uses cores, time limits use seconds. Images continue through the existing trusted Broker configuration path; digest/profile allow-list enforcement belongs to the configuration/security work.

Every newly created resource has `ailab.owner`, `ailab.lease_id`, `ailab.run_id`, `ailab.user_id`, `ailab.created_at` before it is discoverable. Additional metadata freezes spec, wanted state, expiry, maximum expiry, injected-file manifest and preview sequence. Docker's creation labels are immutable: mutable fields are merged from `/run/ailab-lease.json`, written atomically as root with mode 0600, outside `/workspace`. OpenSandbox uses lifecycle metadata; mock uses a file outside its workspace (mock is not an isolation boundary).

`LeaseStore` records wanted state with version CAS: `creating → active → releasing → released`, or `failed` when creation/resources are lost. Redis uses atomic Lua CAS and a live-id set, so unrelated heartbeats do not conflict on a whole-hash WATCH. Live records have no expiry. Confirmed terminal records are retained one hour, then CAS-pruned. The provider decides whether a resource actually exists: inspection returns `None` only for confirmed absence; network/permission failures are errors.

Startup and each reconciliation pass list only this owner group. Existing active resources are adopted. A flushed Redis store is rebuilt from valid active provider metadata, including the renewed deadline. Partially staged, expired, duplicate, terminal or incompletely labelled resources are cleaned up. A missing resource closes its lease and is never recreated under the old handle. Deletion remains `releasing`, occupies capacity, and retries after 2, 4, 8 … 60 seconds until inspection confirms absence. An ambiguous create with no returned reference retains its reservation through the provider create deadline (OSB request timeout + 150 seconds), allowing a late resource to be found and deleted.

Activation is written on the resource after input staging and before the final store CAS. Releasing intent is copied to provider metadata before delete; a failed delete is not success. Simultaneous loss of Redis and failure of both provider metadata update and provider delete cannot preserve an unacknowledged release intent; recovery then retains that resource only until its existing lease/max deadline. This is a single-node failure boundary, not a transactional guarantee across Redis and a provider.

## Public API for Worker/render and Agent integration

```python
# Imports: broker.service (LeaseManager), domain.admission (LeasePrincipal,
# AdmissionDecision), contracts.sandbox (SandboxSpec, LeaseHandle).
LeasePrincipal(user_id: str, run_id: str, system: bool = False)

LeaseManager.acquire(
    self, spec: SandboxSpec, principal: LeasePrincipal, *,
    wait_seconds: float | None = None,
    on_queued: Callable[[AdmissionDecision], None] | None = None,
    prepare: Callable[[dict], None] | None = None,
) -> LeaseHandle
LeaseManager.release(self, handle: str | LeaseHandle, reason: str = "released") -> None
LeaseManager.renew(self, handle: str | LeaseHandle, ttl_seconds: int | None = None) -> dict
LeaseManager.get(self, handle: str | LeaseHandle) -> dict
LeaseManager.status(self, handle: str | LeaseHandle) -> dict
LeaseManager.admission_status(self, run_id: str) -> dict
LeaseManager.cancel_exec(self, handle: str | LeaseHandle) -> bool

# Compatible Agent entry point; inputs is ignored, ownership/attachments come from API.
LeaseManager.create(
    self, run_id: str, inputs: list[dict] | None = None,
    image: str | None = None, ttl_seconds: int | None = None, *, wait_seconds: float = 0,
) -> dict
LeaseManager.exec(self, lease_id: str, cmd: str, timeout: int | None = None) -> ExecResult
LeaseManager.put_file(self, lease_id: str, path: str, data: bytes) -> None
LeaseManager.get_file(self, lease_id: str, path: str) -> bytes
```

Worker C should use a unique `run_id="render:<job_id>"`, the API-authorized owner (or a trusted Worker system principal), `system=True`, `profile="render"`, `memory <= sandbox_render_memory_bytes`, and **both** `ttl` and `max_lifetime <= sandbox_render_max_ttl_seconds`. Use `handle.lease_id` for exec/file calls, and `release(handle)` in `finally`. No Run lookup/input injection/event is required by `acquire`; optional `prepare` runs before activation. A provider delete can remain pending after `release` returns; inspect `status` for confirmation.

HTTP additions, under existing internal service authentication:

- `POST /internal/leases/{lease_id}/renew?ttl_seconds=N` renews an active, unexpired lease; omitted N uses its original TTL. It cannot extend beyond the original maximum lifetime or revive an expired lease.
- `GET /internal/leases/{lease_id}/status` includes desired state and terminal reason. `GET /internal/admission/{run_id}` returns queued position and remaining seconds (or `status: absent`).
- `POST /internal/leases` waits inside the Broker for at most `sandbox_wait_seconds`; it emits `agent.waiting_sandbox` with `position` and `request_id`. Direct Python `create` defaults to immediate admission for compatibility; `acquire` defaults to the configured wait deadline.
- `AsyncBrokerClient` adds `renew`, `lease_status`, `admission_status` and raises its create HTTP deadline to cover Broker queueing and provider creation.

`LeaseLostError` (`lease_lost`) and its `LeaseExpiredError` subtype (`lease_expired`) map to HTTP 410. The runtime should stop the current turn, report a recoverable Run failure, and resume only from its safe checkpoint; never silently acquire and replay an arbitrary tool. `LeaseConflictError` is `lease_conflict`, `BrokerOwnershipError` is `broker_ownership_lost`, and base `LeaseError` is `lease_provider_error`. `AdmissionRejected` is `sandbox_admission_rejected` with `reason`; `AdmissionTimeout` is `sandbox_queue_deadline`. Admission errors inherit `CapacityError` and map to 429. JSON errors have `detail: {code, message, recoverable, reason?}`. State/lifetime/owner failures on live lease routes map to 410/503. Provider faults remain 502 where applicable. The Agent runtime still needs to call heartbeat every 60 seconds while running, including model waits; this patch supplies the Broker/client contract, not runtime scheduling.

Mock cancels tracked subprocess groups. OpenSandbox interrupts known execution ids from the SDK init event; cancellation is best effort before that event or across Broker restart. Docker reports `False` for independent exec cancellation: release the sandbox to stop all its commands. Exec timeouts remain a second bound.

## Single-Broker admission

`CapacityProvider.capacity()` returns a static single-machine total sandbox/memory budget. Total includes reserved render slots and memory; the general class cannot borrow that reserve. A trusted short render job can enter a free reserved slot even when the Work waiting queue is full. `AdmissionPolicy.admit(principal, spec)` returns admitted, queued (request id, 1-based position, no invented ETA), or rejected (reason).

The queue is bounded at 32 waiting requests; admission and creating reservations are serialized under a condition, while store mutations use CAS. FIFO applies among eligible requests in each profile: a user at its cap or a request too large for current free memory does not block eligible users. Per-user counts include creating/active/releasing leases; system render work uses its reserved class. Positions are across both waiting classes; deadlines use monotonic time. Release/reconcile/cancellation notify waiters. Capacity is not polled. Queue entries are ephemeral and callers retry after Broker restart; existing resources/leases are recoverable. An existing active Run lease is idempotently returned; a concurrent create/wait for that Run is rejected explicitly.

A Redis owner lock (TTL 30 seconds, heartbeat every TTL/3) rejects a second live Broker in the same owner group. Lost ownership fences resource operations and readiness. After Redis flush a winner reclaims the lock and reconciles before admitting; provider/Redis failure during recovery fails closed. Startup recovery failure is fatal. Different deployments **must not** point separate owner names at the same capacity budget to bypass this check. This is an accidental-replica guard, not multi-Broker HA or distributed fencing. Do not scale the Broker Deployment or run multiple uvicorn workers. Provider backstops and finite exec timeouts remain necessary for process/node loss.

## Settings and cost

All settings have the `AILAB_` prefix. New fields are grouped next to the existing sandbox settings:

- `broker_owner=single-node`, `broker_lock_ttl_seconds=30`, `broker_reconcile_interval_seconds=10`, `broker_release_retry_seconds=2`, `broker_record_retention_seconds=3600`.
- `sandbox_max_lifetime_seconds=14400`, `sandbox_cpu=1`, `sandbox_memory_bytes=1610612736`, `sandbox_disk_bytes=4294967296`, `sandbox_total_memory_bytes=3221225472`.
- `sandbox_max_per_user=1`, `sandbox_queue_limit=32`, `sandbox_render_reserved_slots=1`, `sandbox_render_memory_bytes=805306368`, `sandbox_render_max_ttl_seconds=120`.
- Existing defaults change to `sandbox_ttl_seconds=1200`, `max_sandboxes=2` (one Work plus one short render). Existing `sandbox_wait_seconds=300` remains. Custom configurations with total=1 must explicitly disable the render reservation or increase total. `.env.example` and the deployment total are aligned. No eager sandbox is created.

Two daemon threads (owner heartbeat and reconciliation), no new dependency or business DB query. Admission scans the bounded queue and live-id set; terminal history is only read during reconciliation/metrics and pruned after retention. Metadata and lease records are proportional to live leases and recent terminal records; snapshots retain the pre-existing storage/byte limits. Docker inspects mutable metadata via a root exec, so each inspection adds runtime calls; measure a real Docker host before production sizing. OpenSandbox lifecycle API cost likewise requires real-server acceptance.

Local macOS diagnostic, mock resources, two live leases, 200 reconciliation passes: memory store 1.401 ms wall / 1.383 ms CPU per pass, 68.8 KiB peak traced Python allocation; real Redis on 16440 plus mock provider 2.476 ms wall / 2.038 ms CPU, 226.3 KiB traced allocation; whole Python process peak RSS 48.7/49.2 MiB. This excludes real sandbox CPU/RSS, Docker/OSB latency, thread stacks and deployment hardware; it is not a 2-vCPU production benchmark. At the default ten-second interval the measured Python reconcile CPU is about 0.02% of one core. Failed resources are retried serially with bounded provider calls and per-lease backoff; large orphan sets can delay a pass, while the independent owner heartbeat stays alive.

Remaining provider limits: Docker has no native TTL controller; Broker downtime delays its expiration cleanup. `disk` is a declared future-provider limit, not enforced by the current mock/Docker/OSB adapters (filesystem/storage-driver quotas require deployment support). No sandbox CPU/disk aggregate scheduler, idle reuse, durable admission queue or multi-node scheduling is claimed. Real Docker and OpenSandbox contract tests opt in with `AILAB_TEST_DOCKER=1` / `AILAB_TEST_OSB_URL`; their absence is a skipped acceptance boundary.

Upgrade boundary: resources created by the old implementation lack an owner group and cannot be safely adopted by scoped `list_owned`. Drain or explicitly remove those legacy containers during the one-time upgrade; this Broker never deletes another group or globally wipes runtime resources.

Validation (2026-10-23): full SQLite + Redis 449 passed / 10 skipped; PostgreSQL 16 + Redis 457 passed / 2 skipped, including 48 fault cases across memory/Redis. The final two skips are real Docker/OpenSandbox contracts; SQLite additionally skips eight PostgreSQL cases. Portal 4 tests and production build passed. Ruff check/format, diff whitespace and dependency-lock checks passed. Temporary Redis 16440 and PostgreSQL 15440 were stopped after verification.
