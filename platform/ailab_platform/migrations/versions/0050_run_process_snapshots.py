"""Durable, independently versioned user-visible process history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "run_process_snapshots",
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("through_seq", sa.Integer(), nullable=False),
        sa.Column("items", sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("uncompressed_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("run_process_snapshots")
