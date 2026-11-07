"""Store native blocks as ranges in one canonical generation text asset."""

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_blocks", sa.Column("byte_offset", sa.Integer(), nullable=True))
    op.add_column("document_blocks", sa.Column("byte_size", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("document_blocks", "byte_size")
    op.drop_column("document_blocks", "byte_offset")
