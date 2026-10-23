# Single-node implementation status

Updated 2026-10-23. This matrix records locally validated capabilities against the frozen target; external-service acceptance remains separate.

| Capability | Current implementation | Validation and remaining scope |
|---|---|---|
| User daily/monthly token quota | Call-level atomic reservations, including other Runs and expired open intents; configurable IANA period timezone | SQLite and PostgreSQL + Redis; PostgreSQL row lock tested without a process lock. No department pool or automatic fallback policy. |
| Usage ledger | Unique call IDs, immutable pricing snapshot and integer milli-unit settlement; estimated/released/unreconciled states and late correction | Duplicate delivery, API outage, stream loss, cancellation, legacy migration and both database query plans tested. Gateway-side reconciliation is outside this change. |
| Run token budget | Soft raw input + output budget read from ledger; check before the next call and before tools; a completed answer may cross it | Existing final-answer, recovery and tool-order regressions retained. |
| Usage read and ops audit | Window reservations, percentage, reset time and alerts; bounded keyset reconciliation CLI | Contracts and limits: [usage-ledger.md](usage-ledger.md). Portal banner rendering belongs to the Portal implementation. |
