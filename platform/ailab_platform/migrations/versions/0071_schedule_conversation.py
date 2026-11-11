"""Scheduled tasks reply inside one conversation (ADR 0040).

``conversation_id`` is the conversation every occurrence continues; NULL until the first occurrence of a task created on
the schedules page (or of an older task) creates it. ``due_at`` holds an occurrence that found its conversation busy and
is waiting to run as soon as the conversation is idle. No foreign key: deleting a conversation deletes its tasks in the
same transaction, like ``last_conversation_id``.
"""

import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0070"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scheduled_tasks", sa.Column("conversation_id", sa.String(40), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_scheduled_tasks_conversation", "scheduled_tasks", ["conversation_id"])


def downgrade():
    op.drop_index("ix_scheduled_tasks_conversation", table_name="scheduled_tasks")
    op.drop_column("scheduled_tasks", "due_at")
    op.drop_column("scheduled_tasks", "conversation_id")
