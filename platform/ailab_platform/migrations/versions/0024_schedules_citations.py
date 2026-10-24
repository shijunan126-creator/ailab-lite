"""Scheduled tasks and their per-user due index."""

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(60), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("skill", sa.String(120)),
        sa.Column("model", sa.String(120)),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id")),
        sa.Column("schedule", sa.JSON(), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True)),
        sa.Column("last_run_id", sa.String(40)),
        sa.Column("last_conversation_id", sa.String(40)),
        sa.Column("last_status", sa.String(20)),
        sa.Column("last_error", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scheduled_tasks_due", "scheduled_tasks", ["enabled", "next_run_at"])
    op.create_index("ix_scheduled_tasks_user", "scheduled_tasks", ["user_id"])
    op.create_index("ix_scheduled_tasks_project", "scheduled_tasks", ["project_id"])


def downgrade():
    op.drop_table("scheduled_tasks")
