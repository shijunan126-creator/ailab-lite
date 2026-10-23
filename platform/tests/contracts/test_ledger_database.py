"""Ledger SQL plans and PostgreSQL admission with the process-local lock removed."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, text

from ailab_platform import locks
from ailab_platform.db import engine as app_engine
from ailab_platform.migrate import run_migrations
from ailab_platform.models import ModelUsage, UsageReservation
from tests.test_ledger_protocol import api  # noqa: F401
from tests.test_ledger_protocol import test_two_runs_share_one_atomic_hard_quota as concurrent_quota
from tests.test_migrations import _seed_user_and_run, engine  # noqa: F401


def test_postgres_hard_quota_uses_a_database_lock(api, monkeypatch):  # noqa: F811
    if app_engine.dialect.name != "postgresql":
        pytest.skip("requires AILAB_TEST_DATABASE_URL pointing at throwaway PostgreSQL")
    monkeypatch.setattr(locks, "_lock_for", lambda _: nullcontext())
    concurrent_quota(api, monkeypatch)


def test_ledger_queries_use_bounded_index_ranges(engine):  # noqa: F811
    run_migrations(engine)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        _seed_user_and_run(conn)
        for batch in range(40):
            conn.execute(
                insert(ModelUsage),
                [
                    {
                        "call_id": f"legacy-{i}",
                        "run_id": "r1",
                        "user_id": f"u-{i % 100}",
                        "created_at": now - timedelta(days=i % 400),
                        "weighted_amount": 1000,
                        "status": "estimated" if i % 997 == 0 else "settled",
                    }
                    for i in range(batch * 200, (batch + 1) * 200)
                ],
            )
            conn.execute(
                insert(UsageReservation),
                [
                    {
                        "call_id": f"call-{i}",
                        "run_id": "r1",
                        "user_id": f"u-{i % 100}",
                        "attempt_id": "test",
                        "mode": "work",
                        "alias": "test-fast",
                        "max_output_tokens": 10,
                        "estimated_input_tokens": 1,
                        "weight": 1,
                        "cached_token_weight": 0.1,
                        "policy_version": "v1",
                        "reserved_amount": 11000,
                        "status": "open" if i % 997 == 0 else "settled",
                        "created_at": now,
                        "expires_at": now,
                    }
                    for i in range(batch * 200, (batch + 1) * 200)
                ],
            )
        conn.execute(text("ANALYZE"))
        queries = [
            (
                "SELECT SUM(weighted_amount) FROM model_usage WHERE user_id = 'u-1' AND created_at >= '2026-10-01'",
                "ix_model_usage_user_created",
            ),
            (
                "SELECT SUM(reserved_amount) FROM usage_reservations WHERE user_id = 'u-1' AND status = 'open'",
                "ix_usage_reservations_user_status",
            ),
            (
                "SELECT call_id FROM usage_reservations WHERE status = 'open' AND expires_at <= '2026-10-31' "
                "ORDER BY expires_at, call_id LIMIT 200",
                "ix_usage_reservations_status_expires",
            ),
            ("SELECT id FROM model_usage WHERE status = 'estimated' AND id > 0 ORDER BY id LIMIT 200", "ix_model_usage_status_id"),
        ]
        prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
        for sql, index in queries:
            plan = str(conn.execute(text(prefix + sql)).all())
            assert index in plan, plan
