"""Durable job publication, acknowledgements and run attempt interface."""

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    for column in (
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default="1970-01-01 00:00:00"),
        sa.Column("last_error", sa.String(120)),
        sa.Column("claimed_until", sa.DateTime(timezone=True)),
        sa.Column("claim_token", sa.String(40)),
        sa.Column("deliveries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("needs_attention", sa.Integer(), nullable=False, server_default="0"),
    ):
        op.add_column("outbox", column)
    op.create_index("ix_outbox_due", "outbox", ["needs_attention", "next_attempt_at", "id"])
    op.create_index("ix_outbox_created_at", "outbox", ["created_at", "id"])
    op.create_table(
        "run_attempts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("agent_instance", sa.String(120), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("outcome", sa.String(40)),
        sa.UniqueConstraint("run_id", "number"),
    )
    op.create_index("ix_run_attempts_run_id", "run_attempts", ["run_id"])

    # Seed legacy active work once in the migration; Redis contents are not durable evidence.
    # Core tables keep JSON portable across SQLite and PostgreSQL without importing ORM/config.
    conn = op.get_bind()
    outbox = sa.table(
        "outbox",
        sa.column("id", sa.String),
        sa.column("topic", sa.String),
        sa.column("payload", sa.JSON),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    deadline = (now + timedelta(days=1)).isoformat()

    def envelope(job_id, kind, **resource):
        return {
            "job_id": job_id,
            "schema_version": 1,
            "kind": kind,
            "attempt": 1,
            "deadline": deadline,
            "enqueued_at": now.isoformat(),
            "request_id": None,
            "traceparent": None,
            **resource,
        }

    for row in conn.execute(sa.select(outbox)):
        payload = row.payload
        if "schema_version" not in payload and payload.get("file_id"):
            conn.execute(
                outbox.update().where(outbox.c.id == row.id).values(payload=envelope(row.id, "preview", file_id=payload["file_id"]))
            )
    runs = sa.table("runs", sa.column("id", sa.String), sa.column("status", sa.String), sa.column("options", sa.JSON))
    # Read the deployment's stream name without loading its .env or initializing application settings.
    import os

    run_queue = os.environ.get("AILAB_RUN_QUEUE", "ailab:stream:runs")
    file_queue = os.environ.get("AILAB_FILE_QUEUE", "ailab:stream:files")
    for row in conn.execute(sa.select(runs).where(runs.c.status.in_(["queued", "running"]))):
        mode = (row.options or {}).get("mode", "work")
        if conn.scalar(sa.select(outbox.c.id).where(outbox.c.id == row.id)):
            continue
        conn.execute(
            outbox.insert().values(
                id=row.id, topic=f"{run_queue}:{mode}", created_at=now, payload=envelope(row.id, "run", run_id=row.id, mode=mode)
            )
        )
    files = sa.table("files", sa.column("id", sa.String), sa.column("status", sa.String), sa.column("meta", sa.JSON))
    for row in conn.execute(sa.select(files).where(files.c.status == "pending")):
        if (row.meta or {}).get("processing") != "awaiting_upload":
            job_id = f"scan_{row.id}"
            if conn.scalar(sa.select(outbox.c.id).where(outbox.c.id == job_id)):
                continue
            conn.execute(
                outbox.insert().values(id=job_id, topic=file_queue, created_at=now, payload=envelope(job_id, "file", file_id=row.id))
            )


def downgrade():
    op.drop_table("run_attempts")
    op.drop_index("ix_outbox_created_at", "outbox")
    op.drop_index("ix_outbox_due", "outbox")
    for name in ("needs_attention", "deliveries", "claim_token", "claimed_until", "last_error", "next_attempt_at", "attempts"):
        op.drop_column("outbox", name)
