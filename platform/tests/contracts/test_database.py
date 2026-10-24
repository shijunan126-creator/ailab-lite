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


def test_inbox_queue_queries_use_indexes(engine):
    run_migrations(engine)
    statements = {
        "ix_notifications_user_created": [
            "SELECT id FROM notifications WHERE user_id='u' ORDER BY created_at DESC, id DESC LIMIT 51",
            "SELECT created_at FROM notifications WHERE user_id='u' ORDER BY created_at DESC, id DESC LIMIT 1",
            "SELECT id FROM notifications WHERE user_id='u' ORDER BY created_at DESC, id DESC LIMIT 200",
        ],
        "ix_notifications_unread": ["SELECT count(*) FROM notifications WHERE user_id='u' AND read_at IS NULL"],
        "ix_notifications_run_kind": ["SELECT id FROM notifications WHERE run_id='r' AND kind='run.waiting' LIMIT 1"],
        "ix_queued_messages_conversation_state": [
            "SELECT id FROM queued_messages WHERE conversation_id='c' AND state='queued' ORDER BY created_at, id LIMIT 5",
            "SELECT id FROM queued_messages WHERE conversation_id='c' AND state='queued' AND steer=true ORDER BY created_at, id LIMIT 1",
        ],
        "ix_queued_messages_run_state": [
            "SELECT id FROM queued_messages WHERE run_id='r' AND state='steered' ORDER BY created_at, id",
        ],
    }
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL enable_seqscan=off"))
        for index, queries in statements.items():
            for query in queries:
                prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
                plan = " ".join(str(row) for row in conn.execute(text(prefix + query)))
                assert index in plan, plan
