# ADR 0004 · Self-developed Agent runtime and platform-owned checkpoints

Status: accepted for the single-node implementation (2026-10-23). Implemented on `t3/runtime-resume-fencing`; see [T07 review](../../reviews/2026-10-23-t07-runtime.md) for evidence and unverified boundaries.

## Context

The target architecture names LangGraph and deepagents as candidate agent frameworks. Adopting one now would move checkpoint state, retries, and interrupt semantics into framework-private formats, and the platform would need that framework to resume a run. The single-node scope needs three guarantees the platform must own regardless of framework: a run survives an Agent crash without repeating a non-replayable command, a question can pause a run without holding a sandbox or an Agent slot, and an old attempt cannot write business state.

The current loop is the only runtime today. Its behaviour is covered by tests, and its model and tool contracts are already platform-owned.

## Decision

1. **Runtime boundary.** `ports/runtime.py` defines `AgentRuntime` with `start`, `resume`, `cancel`, and `snapshot`. The self-developed `AgentLoop` is the only adapter. `adapters/runtime.py` owns the state schema (`RUNTIME_VERSION`, fresh and restored state, `NON_REPLAYABLE`, and the uncertain-exec message). A framework adapter added later must read and write this same platform-owned state, and must not require its own state format.

2. **Checkpoint format.** The full state is platform JSON: messages, step counter, pending tool calls, tool log, plan, artifacts, deliveries, workspace snapshot reference, workspace dirty flag, and the final summary. It is gzipped into `checkpoints/{run_id}/{step}-{uuid}.json.gz`. Each object is immutable. The `run_checkpoints` row and `runs.checkpoint_step` hold the step, attempt, object key, and a plan and tool-log summary. A step advances by compare-and-set; a stale step is rejected. Business rows never contain the full message history.

3. **Ownership fence.** The durable job claim returns an `attempt_id` equal to its claim token. Every Run-scoped business write from the Agent carries that attempt: transition, events, checkpoint, pause, model summary, usage reserve, and artifact delivery. The write is accepted only while the outbox row still holds that token and is unexpired, and the attempt is still open. The check takes the same write lock as claim, ACK, and release, so it holds until the write commits. The fence is `api/runtime_fence.py`.

4. **Settlement identity.** A usage settlement is authenticated by the attempt that reserved the call, carried in the payload (`SettleUsageIn.attempt_id`), not by live ownership. A late settlement from an attempt that has lost the job still reconciles its own call. A settlement from any other attempt is refused as a call-identity mismatch.

5. **Legacy write surfaces.** `/internal/runs/{id}/status` forwards to `/transition` and shares the same fence. `/transition` accepts only `running`, `succeeded`, `failed`, and `canceled` from the Agent. `waiting_user` and `interrupted` are reachable only through `/pause`, which requires a safe checkpoint. `queued` is reachable only through the API (answer or resume). `/internal/runs/{id}/usage` returns 410 and writes nothing. `/internal/runs/{id}/events` is fenced. A legacy queue message without a durable job is acknowledged and dropped with a warning, because every Run has a durable job staged in its creating transaction.

6. **Continuation is a new unit of work.** An answer or resume creates a new job id stored in `runs.runtime_meta.job_id`, and it ends the open attempts as `continued`. An ACK for the old job cannot delete the continuation. A job's attempt counter counts deliveries of that job only, so a continuation starts at 1, and a re-claim after a death is greater than 1. More than `run_max_attempts` deliveries of one job fails the run instead of looping.

7. **Lease loss.** The Agent renews its sandbox lease on its own clock (`runtime_heartbeat_seconds`, default 60, capped at 60). Only the Broker's 410 counts as loss. Other renewal failures are retried, and the Broker's expiry eventually answers 410. Loss interrupts the run after a safe checkpoint, and the platform continues it automatically once. A second loss, or any loss after the user has already continued the run, waits for the user.

## Consequences

- **Cost.** Each model response and each tool result writes one gzipped checkpoint object, at most `checkpoint_max_bytes` (4 MiB) uncompressed. Each write is one business transaction with one row insert and one CAS update. Old checkpoints are deleted after the second newest, and all of a run's checkpoints are deleted 7 days after it finishes. Lifecycle cleans unreferenced objects after the grace period. The measured cost is in the review.
- **No framework lock-in.** Replacing the loop requires implementing four methods and reading the same state. No framework private state exists to migrate.
- **Restrictions.** The Agent is the only writer of run state. The platform cannot resume a run whose checkpoint is missing or from another runtime version; it fails explicitly. Only the single-node SQLite and PostgreSQL deployments are in scope; the claim serialisation relies on the same database as the business state.
- **Unverified.** Real Docker and OpenSandbox lease loss, Broker restart during a checkpoint write, and resume on a 2 vCPU / 8 GiB machine are not covered by this decision's evidence. Those boundaries are listed in the review.
