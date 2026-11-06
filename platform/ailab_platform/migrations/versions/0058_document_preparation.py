"""Freeze pre-run preparation identity, deadline and input strategy across send retries."""

import sqlalchemy as sa
from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_preparations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("send_id", sa.String(120), nullable=False),
        sa.Column("identity", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.UniqueConstraint("user_id", "send_id", name="uq_document_preparation_send"),
    )
    op.create_index("ix_document_preparations_expiry", "document_preparations", ["expires_at", "id"])
    op.create_index("ix_document_preparations_run", "document_preparations", ["run_id"])


def downgrade():
    op.drop_table("document_preparations")
