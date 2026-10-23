"""Platform-owned runtime and delivery metadata (integration reparents to 0018)."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_runs_waiting", "runs", ["status", "waiting_since", "id"])
    op.add_column("runs", sa.Column("checkpoint_step", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("runs", sa.Column("runtime_meta", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "run_checkpoints",
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("step", sa.Integer(), primary_key=True),
        sa.Column("attempt_id", sa.String(40), nullable=False),
        sa.Column("state_key", sa.String(500), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("tool_log", sa.JSON(), nullable=False),
        sa.Column("deliveries", sa.JSON(), nullable=False),
        sa.Column("runtime_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "run_deliveries",
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("delivery_id", sa.String(160), primary_key=True),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    op.create_index("ix_run_checkpoints_state_key", "run_checkpoints", ["state_key"])
    op.create_index("ix_run_checkpoints_expiry", "run_checkpoints", ["expires_at", "run_id", "step"])
    op.create_index("ix_outbox_claim_token", "outbox", ["claim_token"])


def downgrade():
    op.drop_index("ix_outbox_claim_token", table_name="outbox")
    op.drop_table("run_deliveries")
    op.drop_table("run_checkpoints")
    op.drop_index("ix_runs_waiting", table_name="runs")
    op.drop_column("runs", "waiting_since")
    op.drop_column("runs", "runtime_meta")
    op.drop_column("runs", "checkpoint_step")
