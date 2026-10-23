# Usage ledger service contract

Implemented on `arch/w1-ledger` from `6666573`, migration `0009` (parent `0008`). Platform API owns reservations, pricing, ownership and settlement. Internal routes retain the existing `service_auth` dependency; the service-auth work must allow Agent access to the three live usage routes below. Browser clients only call `GET /v1/usage` through the Portal's existing proxy.

All shown fields are exact. Internal POST DTOs reject unknown fields, including `user_id`. Token counts are nonnegative integers; cached tokens are a subset of input, and reasoning tokens a subset of output. Reasoning is never added to output a second time. Amounts in ledger responses are integer milli-units (`1000 = 1 weighted token`); window values are weighted tokens with at most three decimal places. All reset/expiry times are ISO 8601 UTC.

## Reserve before dispatch

`POST /internal/usage/reserve` → HTTP 200:

```json
{
  "call_id": "call-123",
  "run_id": "run-123",
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
  "attempt_id": "attempt-1",
  "status": "open",
  "reserved_amount": 20000,
  "weight": "1.000000000",
  "cached_token_weight": "0.100000000",
  "policy_version": "v1",
  "expires_at": "2026-10-23T05:00:00Z"
}
```

`subject` is `run` (default) or `file_job`. A `run` reservation carries `run_id`; `mode` is `chat|work` and must match the Run, and only the Agent may reserve it. A `file_job` reservation carries `file_id` with no `run_id`, uses `mode=ocr`, and is created only by the API's OCR service (HTTP reserve/settle return 403); it charges the file owner's user pool under `call_id` `ocr:<file_id>:<page>`. `alias` must be available in the catalog. Fallback callers can reserve a different catalog alias from the same user's pool at its own price. Ownership comes only from the Run. The API serializes check + insert + commit under `user_critical_section` (PostgreSQL user row lock across processes; SQLite single API process). Identical retries return the same reservation, including after Run termination, price changes or expiry; a different payload with the same call ID conflicts. Status may subsequently be `settled|estimated|released|unreconciled`.

Reservation is `(estimated input + max output) × weight`, assuming no cache discount (or the more expensive cached price if greater than 1). Agent uses UTF-8 serialized message/tool bytes + 256 as a conservative input bound, and forwards the configured output bound as upstream `max_tokens`. No model-specific tokenizer is installed.

Limits: call/attempt IDs 1–80 characters, run ID 1–40, alias 1–120, max output 1–10,000,000, input estimate 0–10,000,000. Pricing snapshots use 9 fractional decimal places; integer milli amounts round upward once per call.

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

`actual_backend` and all counts default to empty string/zero; counts are at most 100,000,000 and backend at most 120 characters. Status is required: `settled` for upstream usage, `estimated` for missing usage (input byte bound and ceil(observed output UTF-8 bytes / 4), explicitly approximate), or `released` only when the request never reached the model. Released requests must have all counts zero. A cancellation after dispatch is estimated or settled, never silently free.

Formula: `ceil(((input − cached) + cached × frozen_cached_weight + output) × frozen_model_weight × 1000)`. The same call never creates another ledger row; first settlement wins even if a retry changes counts. An `unreconciled` row is the one exception: a late settle updates it in place once. `run_tokens` is the current ledger raw input + output sum for the whole Run, including earlier attempts; this live aggregate can change after another call settles. The immutable call fields and amounts do not.

Reserve and settle share one bounded retry policy: at most 4 HTTP attempts, 5 seconds total deadline per attempt, delays 0.2/0.4/0.8 seconds (maximum 21.4 seconds). Retry on transport timeout/failure or HTTP 408/429/500/502/503/504 with the same payload and call ID. Exhausted settlement stops Agent execution before tools or another model call. The intent remains charged. Each reaper pass converts at most 200 expired open intents to `unreconciled` at the reserved amount. A late settlement works after Run termination.

Metered ambiguous model transport failures do not replay an inference invisibly. A definite reasoning-format HTTP 400 can still be corrected and retried with the same call ID because no inference succeeded. New Run executions generate fresh attempt and call IDs. Upstream calls carry `x-call-id`, `x-run-id`, and existing `x-user-id`.

## Read contracts

`GET /internal/runs/{run_id}/usage` → HTTP 200 (Agent, no request body):

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

`used` is solely `SUM(weighted_amount)/1000` for calls started in the window; `reserved` includes all open calls, even expired or started before the window. Expiry alone never frees quota. Conversion to the ledger preserves the call-start timestamp. The API reads both tables in one SQL statement/snapshot, avoiding transient double counting during settlement. `percent` uses `(used + reserved)/limit × 100`, rounded to 2 decimals; it is not capped at 100. With zero limit, percent is 100 when there is consumption, otherwise zero; the quota is still closed. Each window emits at most one alert: `exceeded` at ≥100%, otherwise `near_limit` at ≥80%. Empty alerts is `[]`. `library_bytes` and `resets_at_utc` remain for existing Portal clients. UTC reset instants are derived from calendar day/month boundaries in `AILAB_QUOTA_TIMEZONE`, including DST.

## Errors and removed route

FastAPI errors retain the `detail` envelope. Quota denial example:

```json
{"detail":{"code":"quota_exceeded","window":"daily_tokens","resets_at":"2026-10-24T00:00:00+00:00"}}
```

- 429 `quota_exceeded`, window `daily_tokens|monthly_tokens`, UTC `resets_at` (also at Run creation).
- 409 `call_conflict` for same-ID/different-intent or mode mismatch (the latter adds `message: "mode differs from run"`).
- 409 `run_not_active` for a new intent on a terminal Run.
- 400 `model_unavailable` for an unavailable/unknown catalog alias.
- 404 `reservation_not_found` for settle without reserve; unknown Run reads/reserve retain `{"detail":"run not found"}`.
- 422 standard FastAPI validation error for malformed bodies/counts/unknown fields.
- Existing service authentication remains 403; user endpoint without login remains 401.
- Old blind `POST /internal/runs/{run_id}/usage` is retired with HTTP 410 `{"detail":{"code":"usage_reservation_required"}}`. Deploy API and Agent together; retaining blind writes would bypass hard quota. Old client `record_usage` methods are replaced by reserve/settle/get_run_usage.

## Migration, operations and cost

`0009_ledger.py` directly adds columns and indexes without rebuilding referenced tables. Legacy rows get `call_id=legacy-{id}`, run-derived user/mode, alias from old model, cached discount from migration-time configuration, integer amount and `policy_version=legacy`. Actual backend, reasoning and department are historically unknown. Backfill is keyset-batched at 500 rows; it runs once. Downgrade removes reservations/new dimensions but preserves old usage/run/event rows. In-flight accounting is lost on downgrade; stop Agents first. Upgrade API/Agent and the service-auth route allowlist as one deployment.

Settings added in `config.py` (small additive change owned by B): `AILAB_QUOTA_TIMEZONE=UTC` (IANA validated), `AILAB_USAGE_RESERVATION_TTL_SECONDS=3600` (60–86400), `AILAB_MODEL_MAX_OUTPUT_TOKENS=8192` (1–10,000,000). Cached weight now validates finite 0–100. Keep reservation TTL beyond the configured complete Run deadline; an earlier expiry is conservative and late settlement still corrects it.

From the API environment:

```bash
python -m ailab_platform.ops.reconcile_usage --batch-size 200 --after-usage-id 0 --after-run-id ''
```

Output is `{"usage":[{"id":1,"call_id":"…","run_id":"…","status":"estimated","weighted_amount":9500}],"run_mismatches":[{"run_id":"…","recorded":{},"ledger":{}}],"next_usage_id":1,"next_run_id":"…"}`. Actual recorded/ledger objects use the six aggregate fields above; recorded may be null for legacy Runs. Resume each independent keyset cursor until it becomes null. Batch is 1–1000, default 200. It performs two indexed suspect-row queries and one bounded Run projection joined to a grouped aggregate in a single database snapshot, with no full result JSON materialization or automatic repair.

Normal call: 2 transactions, 4 business writes (reservation INSERT; ledger INSERT + reservation UPDATE + Run total UPDATE). Compared with the old single INSERT/transaction this adds 3 writes and one commit. Idempotent settled retries add no business write. No network/model I/O under the user lock. Reaper costs 3 business writes and one commit per expired call, bounded at 200 per pass. No new dependency or resident service.

Measured on this macOS development machine with local SQLite WAL and PostgreSQL 16, 300 calls each (not a production SLO): lock p50/p95 SQLite reserve 0.87/1.10 ms, settle 1.02/1.23 ms; PostgreSQL reserve 1.51/1.87 ms, settle 1.76/2.26 ms. Locks exclude wait-to-acquire. Window read p95 0.70/0.87 ms. A 200-row expiry pass took 247/377 ms. Whole probe CPU 1.19/1.62 s; process peak RSS 94/100 MiB, incremental peak growth 1.25/0.97 MiB, including the existing Python stack. Synthetic 8,000-row SQLite/PostgreSQL EXPLAIN checks verify indexes for window sum, open reservations, expiry, and suspect-row keysets; sum work still scales with a user's period history. Both backend tables retain one reservation and one ledger row per call plus their indexes; retention/aggregate rollups remain future work.

Known boundaries: hard admission assumes the configured upstream honors `max_tokens` and the conservative input bound; model-specific tokenizers and real paid upstream enforcement were not validated. Missing stream usage is approximate and cannot recover unseen output after a disconnect; operators see `estimated` rows. Gateway-side metrics matching, department pools and Portal banners are separate tasks. SQLite concurrency is for one API process; PostgreSQL is required for several API writers. Existing ModelUsage model weight stays a compatibility Float; pricing is computed from reservation Decimal snapshots and immutable integer amounts. Call/user columns stay nullable for historical ORM compatibility, but all migration and API-created rows receive both. Legacy Runs without stored totals surface as reconciliation mismatches. Reaper/retention work must expire intents before deleting their referenced Runs and retain/freeze ledger ownership.
