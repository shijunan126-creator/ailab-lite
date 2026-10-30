"""Store user UI snapshots separately from agent results."""

import sqlalchemy as sa
from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("ui_state", sa.JSON(none_as_null=True), nullable=True))


def downgrade():
    op.drop_column("runs", "ui_state")
