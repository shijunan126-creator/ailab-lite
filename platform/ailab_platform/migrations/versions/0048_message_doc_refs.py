"""Frozen document selections, separate from attachment identities."""

import sqlalchemy as sa
from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "message_doc_refs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("message_kind", sa.String(30), nullable=False),
        sa.Column("message_id", sa.String(40), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("file_id", sa.String(40), nullable=True),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("filename", sa.String(500), nullable=False),
        sa.UniqueConstraint("message_kind", "message_id", "ordinal", name="uq_message_doc_ref_ordinal"),
    )
    op.create_index("ix_message_doc_refs_run", "message_doc_refs", ["run_id"])
    op.create_index("ix_message_doc_refs_file", "message_doc_refs", ["file_id"])

    op.create_table(
        "viewer_captures",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.String(40), nullable=True),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("storage_key", sa.String(600), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_viewer_captures_expiry", "viewer_captures", ["expires_at"])
    op.create_index("ix_viewer_captures_run", "viewer_captures", ["run_id"])


def downgrade():
    op.drop_table("viewer_captures")
    op.drop_table("message_doc_refs")
