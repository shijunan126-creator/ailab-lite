"""Project instructions."""

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("projects", sa.Column("instructions", sa.Text(), nullable=False, server_default=""))


def downgrade():
    op.drop_column("projects", "instructions")
