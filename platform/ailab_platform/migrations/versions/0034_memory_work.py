"""Durable memory progress and fenced background claims."""

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_runs_memory_history", "runs", ["user_id", "created_at", "id"])
    op.create_index("ix_runs_memory_index", "runs", ["status", "created_at", "id"])
    op.add_column("conversations", sa.Column("memory_policy", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "memory_work_items",
        sa.Column("run_id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("source_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_end", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_memory_work_user_state_run", "memory_work_items", ["user_id", "state", "run_id"])
    op.add_column("memory_dream_runs", sa.Column("claim_token", sa.String(40), nullable=True))
    op.add_column("memory_dream_runs", sa.Column("claimed_until", sa.DateTime(timezone=True), nullable=True))
    for table in ("usage_reservations", "model_usage"):
        op.add_column(table, sa.Column("job_id", sa.String(40), nullable=True))


def downgrade():
    op.drop_index("ix_runs_memory_index", table_name="runs")
    op.drop_index("ix_runs_memory_history", table_name="runs")
    for table in ("model_usage", "usage_reservations"):
        op.drop_column(table, "job_id")
    op.drop_column("memory_dream_runs", "claimed_until")
    op.drop_column("memory_dream_runs", "claim_token")
    op.drop_table("memory_work_items")
    op.drop_column("conversations", "memory_policy")
