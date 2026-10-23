"""Persist time in RUNNING without replaying events or estimating legacy history."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    # Existing rows stay NULL: old event retention may have removed pauses.
    op.add_column("runs", sa.Column("work_ms", sa.BigInteger(), nullable=True))
    op.add_column("runs", sa.Column("work_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("runs", "work_started_at")
    op.drop_column("runs", "work_ms")
