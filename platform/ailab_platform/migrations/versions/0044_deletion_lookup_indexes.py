"""Index reverse file dependencies used by lineage deletion."""

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_artifacts_file_id", "artifacts", ["file_id"])


def downgrade():
    op.drop_index("ix_artifacts_file_id", table_name="artifacts")
