"""Bounded conversation memory. Integration supplies revision 0014."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("conversations", sa.Column("summary_through_run_id", sa.String(40), nullable=True))
    op.create_index("ix_runs_conversation_created_id", "runs", ["conversation_id", "created_at", "id"])


def downgrade():
    op.drop_index("ix_runs_conversation_created_id", table_name="runs")
    op.drop_column("conversations", "summary_through_run_id")
    op.drop_column("conversations", "summary")
