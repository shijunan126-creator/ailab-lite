"""Fence user actions against conversation branch changes, not ordinary continuation."""

import sqlalchemy as sa
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("branch_revision", sa.Integer(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("conversations", "branch_revision")
