"""Tag account memory lines with the project they were learned in, so a mode switch can move them (ADR 0036)."""

import sqlalchemy as sa
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

# One project per source: '' stands for an account conversation, a run without a conversation or a purged run.
_SOURCE_PROJECT = (
    "SELECT {agg}(COALESCE(c.project_id, '')) FROM memory_item_sources s "
    "LEFT JOIN runs r ON r.id = s.run_id LEFT JOIN conversations c ON c.id = r.conversation_id "
    "WHERE s.memory_id = memory_items.id"
)


def upgrade():
    # Plain ADD COLUMN: no foreign key, no index, no SQLite table rebuild. A deleted project nulls its tags in code.
    op.add_column("memory_items", sa.Column("origin_project_id", sa.String(40), nullable=True))
    # Backfill account lines only, and only when every source points at the same existing project.
    low, high = _SOURCE_PROJECT.format(agg="MIN"), _SOURCE_PROJECT.format(agg="MAX")
    op.execute(f"UPDATE memory_items SET origin_project_id = ({low}) WHERE project_id IS NULL AND ({low}) <> '' AND ({low}) = ({high})")


def downgrade():
    op.drop_column("memory_items", "origin_project_id")
