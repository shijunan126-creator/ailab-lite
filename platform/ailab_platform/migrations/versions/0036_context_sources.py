"""Immutable summary provenance and deletion commit fence."""

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("context_source_epoch", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "file_source_invalidations",
        sa.Column("file_id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("cursor", sa.String(256), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_file_invalidation_state", "file_source_invalidations", ["state", "file_id"])
    op.create_index("ix_file_invalidations_user", "file_source_invalidations", ["user_id", "file_id"])
    op.add_column("users", sa.Column("context_source_epoch", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("conversations", sa.Column("summary_frame_id", sa.String(40)))
    op.create_table(
        "context_summary_frames",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("branch_anchor", sa.String(40), nullable=False),
        sa.Column("previous_id", sa.String(40)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("from_run_id", sa.String(40)),
        sa.Column("through_run_id", sa.String(40), nullable=False),
        sa.Column("source_epoch", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("revalidation_state", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_context_frames_branch", "context_summary_frames", ["conversation_id", "branch_anchor", "revision"])
    op.create_table(
        "context_summary_sources",
        sa.Column("frame_id", sa.String(40), sa.ForeignKey("context_summary_frames.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("file_id", sa.String(40), primary_key=True),
    )
    op.create_index("ix_context_sources_file", "context_summary_sources", ["file_id", "frame_id"])


def downgrade():
    op.drop_column("runs", "context_source_epoch")
    op.drop_table("file_source_invalidations")
    op.drop_table("context_summary_sources")
    op.drop_table("context_summary_frames")
    op.drop_column("conversations", "summary_frame_id")
    op.drop_column("users", "context_source_epoch")
