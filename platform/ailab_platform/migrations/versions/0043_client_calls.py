"""Persist browser client calls and their claim deadlines."""

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "client_calls",
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("call_id", sa.String(160), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("claim_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
    )
    op.create_index("ix_client_calls_run_status", "client_calls", ["run_id", "status"])


def downgrade():
    op.drop_index("ix_client_calls_run_status", table_name="client_calls")
    op.drop_table("client_calls")
