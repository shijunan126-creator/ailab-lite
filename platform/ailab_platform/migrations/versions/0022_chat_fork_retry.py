"""Fork provenance, retry fencing and global context policy."""

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("superseded_by_run_id", sa.String(40), nullable=True))
    op.add_column("runs", sa.Column("retry_of", sa.String(40), nullable=True))
    op.create_index("ix_runs_superseded_by_run_id", "runs", ["superseded_by_run_id"])
    op.add_column("conversations", sa.Column("forked_from", sa.JSON(), nullable=True))
    op.create_table("platform_settings", sa.Column("key", sa.String(80), primary_key=True), sa.Column("value", sa.JSON(), nullable=False))


def downgrade():
    op.drop_table("platform_settings")
    op.drop_column("conversations", "forked_from")
    op.drop_index("ix_runs_superseded_by_run_id", table_name="runs")
    op.drop_column("runs", "retry_of")
    op.drop_column("runs", "superseded_by_run_id")
