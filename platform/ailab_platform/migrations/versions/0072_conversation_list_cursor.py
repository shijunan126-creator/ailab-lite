"""Index live conversation lists by project and page position."""

import sqlalchemy as sa
from alembic import op

revision = "0072"
down_revision = "0071"
branch_labels = None
depends_on = None

INDEX = "ix_conversations_user_project_live"


def upgrade():
    op.drop_index(INDEX, table_name="conversations")
    live = sa.text("deleted_at IS NULL")
    op.create_index(
        INDEX, "conversations", ["user_id", "project_id", "pinned_at", "updated_at", "id"], sqlite_where=live, postgresql_where=live
    )


def downgrade():
    op.drop_index(INDEX, table_name="conversations")
    live_project = sa.text("deleted_at IS NULL AND project_id IS NOT NULL")
    op.create_index(INDEX, "conversations", ["user_id", "project_id"], sqlite_where=live_project, postgresql_where=live_project)
