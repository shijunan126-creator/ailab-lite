"""Bounded lifecycle queries and durable object cleanup intents."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "lifecycle_deletions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(500), nullable=False, unique=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("is_prefix", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_files_status_created", "files", ["status", "created_at", "id"])
    op.create_index("ix_runs_conversation_id_id", "runs", ["conversation_id", "id"])
    op.create_index("ix_conversations_deleted", "conversations", ["deleted_at", "id"])
    op.create_index("ix_runs_finished", "runs", ["status", "finished_at", "id"])
    op.create_index("ix_run_events_created", "run_events", ["created_at", "id"])


def downgrade():
    op.drop_index("ix_run_events_created", "run_events")
    op.drop_index("ix_runs_finished", "runs")
    op.drop_index("ix_conversations_deleted", "conversations")
    op.drop_index("ix_runs_conversation_id_id", "runs")
    op.drop_index("ix_files_status_created", "files")
    op.drop_table("lifecycle_deletions")
