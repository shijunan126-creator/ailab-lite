"""Canonical captures live with message references, without an invented expiry date."""

import sqlalchemy as sa
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade():
    # This leaf table has no inbound foreign keys. SQLite cannot drop NOT NULL
    # in place; the repository's exception for leaf tables applies here.
    with op.batch_alter_table("viewer_captures") as batch:
        batch.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=True)


def downgrade():
    op.execute(sa.text("UPDATE viewer_captures SET expires_at=CURRENT_TIMESTAMP WHERE expires_at IS NULL"))
    with op.batch_alter_table("viewer_captures") as batch:
        batch.alter_column("expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)
