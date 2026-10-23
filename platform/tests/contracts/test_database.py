"""Single-instance DB contract; PostgreSQL uses a private disposable schema."""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from ailab_platform.migrate import run_migrations


def test_migration_transactions_and_foreign_keys(engine):
    run_migrations(engine)
    assert "lifecycle_deletions" in inspect(engine).get_table_names()
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text("INSERT INTO api_tokens (id, user_id, name, token_hash, created_at) VALUES ('bad', 'missing', 't', 'bad', '2026-01-01')")
        )
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM api_tokens")) == 0


def test_gc_queries_have_indexed_plans(engine):
    run_migrations(engine)
    statements = [
        "SELECT id FROM conversations WHERE deleted_at < '2026-01-01' ORDER BY deleted_at, id LIMIT 100",
        "SELECT id FROM runs WHERE status='succeeded' AND finished_at < '2026-01-01' ORDER BY finished_at, id LIMIT 100",
        "SELECT id FROM run_events WHERE created_at < '2026-01-01' ORDER BY created_at, id LIMIT 100",
    ]
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL enable_seqscan=off"))
        for statement in statements:
            plan = " ".join(
                str(row)
                for row in conn.execute(text(("EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN ") + statement))
            )
            assert "INDEX" in plan.upper(), plan


def test_gc_presign_and_conversation_lookup_plans(engine):
    run_migrations(engine)
    statements = [
        "SELECT id FROM files WHERE status='pending' AND created_at < '2026-01-01' ORDER BY created_at, id LIMIT 100",
        "SELECT id FROM runs WHERE conversation_id='c' ORDER BY id LIMIT 100",
    ]
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL enable_seqscan=off"))
        for statement in statements:
            plan = " ".join(
                str(row)
                for row in conn.execute(text(("EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN ") + statement))
            )
            assert "INDEX" in plan.upper(), plan
