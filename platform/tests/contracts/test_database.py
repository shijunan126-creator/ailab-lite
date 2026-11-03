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


def test_file_identity_queries_are_bounded_by_indexes(engine):
    """Representative selective data, ANALYZE, then inspect the production upload query and delivery lookups."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from ailab_platform.api.upload_identity import reusable
    from ailab_platform.models import Artifact, FileObject, Run, UploadReceipt, User

    run_migrations(engine)
    with Session(engine) as db:
        db.add_all([User(id=f"u{i}", email=f"u{i}@test", display_name="test") for i in range(20)])
        db.flush()
        db.add_all([Run(id=f"r{i}", user_id=f"u{i % 20}", prompt="p", title="t", model="m") for i in range(400)])
        db.flush()
        for i in range(2000):
            fid = f"f{i:05}"
            db.add(
                FileObject(
                    id=fid,
                    user_id=f"u{i % 20}",
                    filename=f"report{i}.txt",
                    content_type="text/plain",
                    size=2,
                    sha256=f"{i:064x}",
                    storage_key=f"objects/{fid}",
                    lineage_id=fid,
                    version=1,
                    is_head=1,
                    status="ready",
                )
            )
        db.flush()
        db.add_all([Artifact(id=f"a{i}", run_id=f"r{i % 400}", file_id=f"f{i:05}", output_key="report.txt") for i in range(2000)])
        db.add_all(
            [
                UploadReceipt(
                    user_id=f"u{i % 20}",
                    client_upload_id=f"pick{i}",
                    fingerprint="digest",
                    file_id=f"f{i:05}",
                    reservation_id=f"f{i:05}",
                    reused=0,
                    state="completed",
                )
                for i in range(2000)
            ]
        )
        db.commit()
    with engine.begin() as conn:
        conn.execute(text("ANALYZE"))
    statements = [
        select(UploadReceipt).where(UploadReceipt.file_id == "f00001"),
        select(UploadReceipt).where(UploadReceipt.user_id == "u1", UploadReceipt.client_upload_id == "pick1"),
        select(UploadReceipt).where(UploadReceipt.user_id == "u1", UploadReceipt.reservation_id == "f00001").limit(1),
        select(Artifact).where(Artifact.run_id == "r1", Artifact.output_key == "report.txt").limit(2),
    ]

    # Capture the exact select used by reusable, including its JSON name predicate.
    class Capture:
        def scalar(self, statement):
            statements.append(statement)

    reusable(Capture(), "u1", None, "report1.txt", "text/plain", 2, f"{1:064x}")
    with engine.connect() as conn:
        for statement in statements:
            sql = str(statement.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True}))
            prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
            plan = " ".join(str(row) for row in conn.execute(text(prefix + sql)))
            assert "INDEX" in plan.upper(), plan
            assert "SEQ SCAN" not in plan.upper(), plan
