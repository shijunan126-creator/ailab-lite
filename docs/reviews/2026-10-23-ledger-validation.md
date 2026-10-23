# Call ledger validation

Scope: isolated `/Users/michael/.t3/worktrees/ailab-lite/t3-c4e071eb`, branch `arch/w1-ledger`, base `66665739b4efc0c9d3e95fae05e29afc024ec98d`. No developer `.env`/database, remote writes or other branches used.

Red evidence from an archive of the base: duplicate usage POST produced 2 rows instead of 1; changing cached weight 0.1 → 0.8 changed historical used tokens 210 → 280. The new protocol tests exercise actual reservation and unique settlement rather than preserving that blind endpoint. A later local red regression reproduced workspace cleanup overwriting `usage_totals` (`KeyError`); refresh/merge under the user lock makes it green.

Acceptance includes duplicate/concurrent settlement, repeated intent conflicts, API unreachable for all 4 settlement attempts, conservative reaper and same-row late correction, model/cache price freezing, concurrent Runs at daily/monthly limits, PostgreSQL user row locking without the in-process lock, expiry/period rollover, 80/100% alerts, DST, pre-dispatch release, stream cancellation/estimation/closure, model call headers/output cap, reasoning dimensions, distinct rerun IDs, Run soft final-answer semantics and all three file-entry quota regressions. SQLite/PostgreSQL upgrade, downgrade, legacy backfill and model/migration drift tests pass.

Final validation: Ruff check/format and `git diff --check` clean; SQLite + Redis full suite **405 passed, 11 skipped** (46.38 s); PostgreSQL + Redis full suite **416 passed, 0 skipped** (55.14 s). All skipped SQLite jobs require PostgreSQL. Both final full suites ran concurrently against separate test databases and Redis DBs 15/14.

Real PostgreSQL 16 and Redis use 15410/16410. The suite has isolated `ailab_test`/`ailab_migrations` databases; the performance probe uses a third throwaway `ailab_perf` database. GNU coreutils/findutils precede macOS tools. No paid model, real gateway, Docker/OpenSandbox or S3 acceptance is claimed.

Portal `npm test`: 4 passed. Production `npm run build`: passed. Real Chromium stack E2E: 34 passed in 59.93 seconds, using Redis database 13 and dedicated HTTP ports 18410–18413/13410. No Portal source changes. The existing Starlette/httpx TestClient deprecation warning remains.

Performance (development hardware, not deployment SLO):

| Measurement | SQLite WAL | PostgreSQL 16 |
|---|---:|---:|
| Calls | 300 | 300 |
| Reserve lock p50 / p95 / max ms | 0.874 / 1.098 / 4.319 | 1.513 / 1.873 / 6.634 |
| Settle lock p50 / p95 / max ms | 1.020 / 1.229 / 3.598 | 1.756 / 2.263 / 7.659 |
| Usage read p50 / p95 ms | 0.584 / 0.700 | 0.652 / 0.869 |
| Expire 200 rows ms | 247.434 | 376.537 |
| Whole probe CPU seconds | 1.188 | 1.617 |
| Process peak RSS MiB | 94.14 | 100.14 |
| Incremental peak RSS MiB | 1.25 | 0.97 |

Times exclude lock acquisition wait. No network I/O occurs under the lock. No per-token DB writes. Query plan contract tests populate 8,000 rows per table, ANALYZE, then assert the proper indexes for user window sums, open reservation sums, bounded expiry and status/id suspect scans on both databases. Reaper writes 3 rows/commit per expired intent, at most 200 per pass. CLI candidates and Run aggregates are bounded to 1–1000 rows and keyset-paginated; it does not materialize full Run result JSON or rewrite pricing.

Additional production code outside assigned ownership: additive `config.py` settings/validation and `llm.py` call metadata, output cap and usage capture. In assigned `routes_internal.py`, status/result and workspace cleanup refresh/merge are necessary to preserve the newly recorded totals. Shared tests were adapted to use the new reserve/settle protocol and fake clients; `test_reaper.py` removes reservations before Runs during test cleanup. New architecture status/contract and validation documents accompany the required HANDOFF update. No Portal/deploy/storage/auth/provider work was changed.
