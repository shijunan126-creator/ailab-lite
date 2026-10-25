"""Index live-run keyset pages used by workspace GC."""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_runs_status_id", "runs", ["status", "id"])


def downgrade() -> None:
    op.drop_index("ix_runs_status_id", table_name="runs")
