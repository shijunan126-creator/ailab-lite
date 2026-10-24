"""Transactional inbox and queued messages (parallel branch; parent re-chains)."""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("queue_paused", sa.Boolean(), nullable=False, server_default="0"))
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("conversation_id", sa.String(40)),
        sa.Column("run_id", sa.String(40)),
        sa.Column("share_id", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at", "id"])
    op.create_index(
        "ix_notifications_unread",
        "notifications",
        ["user_id"],
        sqlite_where=sa.text("read_at IS NULL"),
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_index("ix_notifications_run_kind", "notifications", ["run_id", "kind"])
    op.create_table(
        "queued_messages",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("file_ids", sa.JSON(), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("options", sa.JSON(), nullable=False),
        sa.Column("steer", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("after_run_id", sa.String(40)),
        sa.Column("run_id", sa.String(40)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_queued_messages_conversation_state", "queued_messages", ["conversation_id", "state", "created_at", "id"])
    op.create_index("ix_queued_messages_run_state", "queued_messages", ["run_id", "state", "created_at", "id"])


def downgrade():
    op.drop_table("queued_messages")
    op.drop_table("notifications")
    op.drop_column("conversations", "queue_paused")
