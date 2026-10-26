"""Immutable conversation episodes for memory dream extraction."""

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_episodes",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=True),
        sa.Column("conversation_id", sa.String(40), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "content_hash", name="uq_memory_episode_user_hash"),
    )
    op.create_index("ix_memory_episodes_user_id", "memory_episodes", ["user_id"])
    op.create_index("ix_memory_episodes_run_id", "memory_episodes", ["run_id"])
    op.create_index("ix_memory_episode_user_observed", "memory_episodes", ["user_id", "observed_at"])


def downgrade() -> None:
    op.drop_table("memory_episodes")
