# Usage ledger service contract

Platform API owns reservations, pricing, ownership and settlement. Internal routes authenticate with `X-Service-Token` and the `usage:reserve`, `usage:write` and `runs:read` scopes (`platform/ailab_platform/service_auth.py::SERVICE_SCOPES`). Browser clients only call `GET /v1/usage` through the Portal's same-origin proxy. Schema: `platform/ailab_platform/migrations/versions/0009_ledger.py`, extended by revisions `0018`, `0023`, `0029` and `0034`.

All shown fields are exact. Internal POST DTOs reject unknown fields, including `user_id`. Token counts are nonnegative integers; cached tokens are a subset of input, and reasoning tokens a subset of output. Reasoning is never added to output a second time. Amounts in ledger responses are integer milli-units (`1000 = 1 weighted token`); window values are weighted tokens with at most three decimal places. All reset/expiry times are ISO 8601 UTC.

## Reserve before dispatch

`POST /internal/usage/reserve` → HTTP 200:

```json
{
  "call_id": "call-123",
  "run_id": "run-123",
  "subject": "run",
  "file_id": null,
  "attempt_id": "attempt-1",
  "alias": "test-fast",
  "mode": "work",
  "max_output_tokens": 10,
  "estimated_input_tokens": 10
}
```

```json
{
  "call_id": "call-123",
  "run_id": "run-123",
  "subject": "run",
  "file_id": null,
  "attempt_id": "attempt-1",
  "status": "open",
  "reserved_amount": 20000,
  "weight": "1.000000000",
  "cached_token_weight": "0.100000000",
  "policy_version": "v1",
  "expires_at": "2026-10-23T05:00:00Z"
}
```

`subject` is `run` (default), `file_job`, `memory_job` or `skill_draft`. A `run` reservation carries `run_id`, uses `mode=chat|work`, must match the Run's mode, and is the only subject the HTTP routes accept; only the Agent has `usage:reserve`. It is also fenced by the attempt that currently owns the Run's durable job. A `file_job` carries `file_id` with no `run_id` and `mode=ocr|summary`; the library summarizer creates `summary` file jobs. A `memory_job` or `skill_draft` carries `job_id` with no run or file, uses `mode=memory` (a memory job may also use `summary`), and is bound to that background claim. The API's summary, memory-dream and skill-draft tasks call `quota.reserve_usage`/`settle_usage` in-process, so these reservations cannot be forged through a service credential; HTTP reserve rejects a non-run subject with 403, and settle does the same when that reservation exists. `alias` must be available in the catalog. Fallback callers can reserve a different catalog alias from the same user's pool at its own price. Ownership comes from the Run, file, memory job or skill draft, never from the payload. The API serializes check + insert + commit under `user_critical_section` (PostgreSQL user row lock across processes; SQLite single API process). Identical retries return the same reservation while the attempt still holds the job, even if the Run has already reached a terminal status; the same call ID with a different payload conflicts, and a different attempt is rejected as `stale_attempt`. Once the job is released or acknowledged, the call ID cannot reserve again. Status may subsequently be `settled|estimated|released|unreconciled`.

Reservation is `(estimated input + max output) × weight`, assuming no cache discount (or the more expensive cached price if greater than 1). Agent estimates input from the serialized request: each non-ASCII character counts as one token, ASCII text as one token per four bytes, plus a fixed framing allowance and `vision_input_tokens` per image (`platform/ailab_platform/model_policy.py::TokenEstimator`). It forwards the configured output bound as upstream `max_tokens`. No model-specific tokenizer is installed.

Limits: `call_id` 1–80 characters, `attempt_id` 1–80 on reserve and 1–40 on settle, run/job/file IDs 1–40, alias 1–120, max output 1–10,000,000, input estimate 0–10,000,000. Pricing snapshots use 9 fractional decimal places; integer milli amounts round upward once per call.

## Settle after dispatch

`POST /internal/usage/settle` → HTTP 200:

```json
{
  "call_id": "call-123",
  "actual_backend": "backend-revision-1",
  "input_tokens": 10,
  "cached_tokens": 5,
  "output_tokens": 4,
  "reasoning_tokens": 2,
  "status": "settled"
}
```

```json
{
  "call_id": "call-123",
  "run_id": "run-123",
  "subject": "run",
  "file_id": null,
  "attempt_id": "attempt-1",
  "mode": "work",
  "alias": "test-fast",
  "actual_backend": "backend-revision-1",
  "input_tokens": 10,
  "cached_tokens": 5,
  "output_tokens": 4,
  "reasoning_tokens": 2,
  "weighted_amount": 9500,
  "policy_version": "v1",
  "status": "settled",
  "run_tokens": 14
}
```

`attempt_id` must equal the reserving attempt; a settle carried by a different attempt returns 409. `actual_backend` and all counts default to empty string/zero; counts are at most 100,000,000 and backend at most 120 characters. Status is required: `settled` for upstream usage, `estimated` for missing usage (input estimate and ceil(observed output UTF-8 bytes / 4), explicitly approximate), or `released` only when the request never reached the model. Released requests must have all counts zero. A cancellation after dispatch is estimated or settled, never silently free.

Formula: `ceil(((input − cached) + cached × frozen_cached_weight + output) × frozen_model_weight × 1000)`. The same call never creates another ledger row; first settlement wins even if a retry changes counts. An `unreconciled` row is the one exception: a late settle updates it in place once. `run_tokens` is the current ledger raw input + output sum for the whole Run, including earlier attempts; this live aggregate can change after another call settles. The immutable call fields and amounts do not.

Reserve and settle share one bounded retry policy: at most 4 HTTP attempts, 5 seconds total deadline per attempt, and 0.2/0.4/0.8 second backoffs with ±50% jitter (about 22 seconds worst case). Retry on transport timeout/failure or HTTP 408/429/500/502/503/504 with the same payload and call ID; a quota refusal is never retried. Exhausted settlement stops Agent execution before tools or another model call. The intent remains charged. Each reaper pass converts at most 200 expired open intents to `unreconciled` at the reserved amount. A late settlement works after Run termination.

Metered ambiguous model transport failures do not replay an inference invisibly. New Run executions generate fresh attempt and call IDs. Upstream calls carry `x-call-id`, `x-run-id`, and existing `x-user-id`.

## Read contracts

`GET /internal/runs/{run_id}/usage` → HTTP 200 (`runs:read`, no request body):

```json
{
  "prompt_tokens": 10,
  "completion_tokens": 4,
  "cached_tokens": 5,
  "reasoning_tokens": 2,
  "weighted_amount": 9500,
  "calls": 1
}
```

These totals are also recorded transactionally as `Run.result.usage_totals` for reconciliation. Existing public Run detail `usage` remains compatible. Run budget is raw input + output, with no extra charge for reasoning already in output. It stops the next model call/tools; an answer in flight may complete beyond it.

`GET /v1/usage` → HTTP 200 (authenticated user, no request body):

```json
{
  "alerts": [
    {"code": "near_limit", "window": "daily_tokens", "resets_at": "2026-10-24T00:00:00+00:00"}
  ],
  "daily_tokens": {
    "used": 1599990.5,
    "reserved": 20000,
    "limit": 2000000,
    "percent": 81.0,
    "resets_at": "2026-10-24T00:00:00+00:00"
  },
  "monthly_tokens": {
    "used": 1599990.5,
    "reserved": 20000,
    "limit": 30000000,
    "percent": 5.4,
    "resets_at": "2026-11-01T00:00:00+00:00"
  },
  "library_bytes": {"used": 0, "limit": 21474836480},
  "resets_at_utc": "2026-10-24T00:00:00+00:00"
}
```

`used` is solely `SUM(weighted_amount)/1000` for calls started in the window; `reserved` includes all open calls, even expired or started before the window. Expiry alone never frees quota. Conversion to the ledger preserves the call-start timestamp. The API reads both tables in one SQL statement/snapshot, avoiding transient double counting during settlement. `percent` uses `(used + reserved)/limit × 100`, rounded to 2 decimals; it is not capped at 100. With zero limit, percent is 100 when there is consumption, otherwise zero, and the quota is closed for any consumption. Each window emits at most one alert: `exceeded` at ≥100%, otherwise `near_limit` at ≥80%. Empty alerts is `[]`. `library_bytes` and `resets_at_utc` remain for existing Portal clients. UTC reset instants are derived from calendar day/month boundaries in `AILAB_QUOTA_TIMEZONE`, including DST.

## Errors and removed route

FastAPI errors retain the `detail` envelope. Quota denial example:

```json
{"detail":{"code":"quota_exceeded","window":"daily_tokens","resets_at":"2026-10-24T00:00:00+00:00"}}
```

- 429 `quota_exceeded`, window `daily_tokens|monthly_tokens`, UTC `resets_at` (also at Run creation).
- 409 `call_conflict` for same-ID/different-intent or mode mismatch (the latter adds `message: "mode differs from run"`).
- 409 `call_identity_mismatch` when a settle's `attempt_id` differs from the reservation.
- 409 `run_not_active` for a new intent on a terminal Run.
- 409 `stale_attempt` when a Run reservation is not carried by the attempt that currently owns its durable job.
- 400 `model_unavailable` for an unavailable/unknown catalog alias.
- 404 `reservation_not_found` for settle without reserve; an unknown Run read keeps `{"detail":"run not found"}` and an unknown reserve subject returns `{"detail":"usage subject not found"}`.
- 403 when HTTP reserve is asked for a file, memory or skill-draft subject (settle returns it when that reservation exists); 404 `{"detail":"usage subject not found"}` when the owning row does not exist.
- 422 standard FastAPI validation error for malformed bodies/counts/unknown fields.
- Existing service authentication remains 403; user endpoint without login remains 401.
- Blind `POST /internal/runs/{run_id}/usage` returns HTTP 410 `{"detail":{"code":"usage_reservation_required"}}`. Deploy API and Agent together; retaining blind writes would bypass hard quota.

## Migration, operations and cost

Revision `0009` (`platform/ailab_platform/migrations/versions/0009_ledger.py`) adds the ledger columns, `usage_reservations` and their indexes without rebuilding referenced tables. Revision `0018` adds `subject`/`file_id` and relaxes `run_id` on the two leaf tables; `0023` adds `quiet`; `0029` adds the department snapshot; `0034` adds `job_id`. Legacy rows get `call_id=legacy-{id}`, run-derived user/mode, alias from old model, cached discount from migration-time configuration, integer amount and `policy_version=legacy`. Actual backend, reasoning and department are historically unknown. Backfill is keyset-batched at 500 rows. Downgrade preserves old usage/run/event rows but loses reservations and in-flight accounting; stop Agents first. Upgrade API and Agent together.

Settings in `platform/ailab_platform/config.py`: `AILAB_QUOTA_TIMEZONE=UTC` (IANA validated), `AILAB_USAGE_RESERVATION_TTL_SECONDS=3600` (60–86400), `AILAB_MODEL_MAX_OUTPUT_TOKENS` (optional operator ceiling, unset by default; 1–10,000,000 when supplied). Each model declares `max_output_tokens` in the catalog; that effective per-call ceiling is used for both reservation and the upstream request. `AILAB_CACHED_TOKEN_WEIGHT` validates as finite 0–1. Keep reservation TTL beyond the configured complete Run deadline; an earlier expiry is conservative and late settlement still corrects it.

From the API environment:

```bash
python -m ailab_platform.ops.reconcile_usage --batch-size 200 --after-usage-id 0 --after-run-id ''
```

Output is `{"usage":[{"id":1,"call_id":"…","run_id":"…","status":"estimated","weighted_amount":9500}],"run_mismatches":[{"run_id":"…","recorded":{},"ledger":{}}],"next_usage_id":1,"next_run_id":"…"}`. Actual recorded/ledger objects use the six aggregate fields above; recorded may be null for legacy Runs. Resume each independent keyset cursor until it becomes null. Batch is 1–1000, default 200. It performs two indexed suspect-row queries and one bounded Run projection joined to a grouped aggregate in a single database snapshot, with no full result JSON materialization or automatic repair.

Normal call: 2 transactions, 4 business writes (reservation INSERT; ledger INSERT + reservation UPDATE + Run total UPDATE). Idempotent settled retries add no business write. No network/model I/O under the user lock. Reaper costs 3 business writes and one commit per expired call, bounded at 200 per pass. No new dependency or resident service. Indexes cover the window sum, open reservations, expiry and suspect-row keysets; sum work still scales with a user's period history. Both tables retain one reservation and one ledger row per call plus their indexes; retention/aggregate rollups remain future work.

Known boundaries: hard admission assumes the configured upstream honors `max_tokens` and the input estimate; model-specific tokenizers and real paid upstream enforcement are not validated. Missing stream usage is approximate and cannot recover unseen output after a disconnect; operators see `estimated` rows. Gateway-side metrics matching is a separate task. SQLite concurrency is for one API process; PostgreSQL is required for several API writers. Existing ModelUsage model weight stays a compatibility Float; pricing is computed from reservation Decimal snapshots and immutable integer amounts. Call/user columns stay nullable for historical ORM compatibility, but all migration and API-created rows receive both. Legacy Runs without stored totals surface as reconciliation mismatches. Reaper/retention work must expire intents before deleting their referenced Runs and retain/freeze ledger ownership.
