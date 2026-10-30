"""User-scoped, indexed message submission identity."""

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("client_send_id", sa.String(64), nullable=True))
    op.create_index("uq_runs_user_client_send", "runs", ["user_id", "client_send_id"], unique=True)


def downgrade():
    op.drop_index("uq_runs_user_client_send", table_name="runs")
    op.drop_column("runs", "client_send_id")
