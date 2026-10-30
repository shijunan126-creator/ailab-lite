"""Persist editable skill drafts and their background claims."""

import sqlalchemy as sa
from alembic import op

revision = "0041"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "skill_drafts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("conversation_id", sa.String(40)),
        sa.Column("base_file_id", sa.String(40)),
        sa.Column("base_kind", sa.String(20)),
        sa.Column("base_name", sa.String(120)),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(20)),
        sa.Column("error", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("files", sa.JSON(), nullable=False),
        sa.Column("claim_token", sa.String(40)),
        sa.Column("claimed_until", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target", sa.String(120)),
        sa.Column("result", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_skill_drafts_user_status_updated", "skill_drafts", ["user_id", "status", "updated_at"])


def downgrade():
    op.drop_index("ix_skill_drafts_user_status_updated", table_name="skill_drafts")
    op.drop_table("skill_drafts")
