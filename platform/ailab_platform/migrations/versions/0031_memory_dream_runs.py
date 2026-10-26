"""Nightly memory dream runs (background consolidation, never a visible Run)."""

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_dream_runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("local_date", sa.String(10), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Asia/Shanghai"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("trigger", sa.String(20), nullable=False, server_default="cron"),
        sa.Column("cursor_run_id", sa.String(40), nullable=True),
        sa.Column("input_run_ids", sa.JSON(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("journal", sa.Text(), nullable=False, server_default=""),
        sa.Column("before_memory", sa.JSON(), nullable=False),
        sa.Column("after_memory", sa.JSON(), nullable=False),
        sa.Column("token_usage", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "local_date", name="uq_memory_dream_user_date"),
    )
    op.create_index("ix_memory_dream_runs_user_id", "memory_dream_runs", ["user_id"])
    op.create_index("ix_memory_dream_runs_status", "memory_dream_runs", ["status"])
    op.create_index("ix_memory_dream_user_finished", "memory_dream_runs", ["user_id", "finished_at"])
    op.create_index("ix_memory_dream_status_created", "memory_dream_runs", ["status", "created_at"])


def downgrade() -> None:
    op.drop_table("memory_dream_runs")
