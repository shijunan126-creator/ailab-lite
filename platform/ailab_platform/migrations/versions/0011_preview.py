"""Preview request outbox (preview manifests remain in files.meta).

Revision ID: 0011
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("topic", sa.String(120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("outbox")
