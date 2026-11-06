"""Bound historical capture pagination within a run without a repeated sort."""

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("ix_viewer_captures_run", table_name="viewer_captures")
    op.create_index("ix_viewer_captures_run", "viewer_captures", ["run_id", "id"])


def downgrade():
    op.drop_index("ix_viewer_captures_run", table_name="viewer_captures")
    op.create_index("ix_viewer_captures_run", "viewer_captures", ["run_id"])
