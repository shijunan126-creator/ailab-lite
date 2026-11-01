"""Admin-managed platform skills and following/customized subscriptions."""

import sqlalchemy as sa
from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "platform_skills",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(40), unique=True, nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(120), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(2048), nullable=False, server_default=""),
        sa.Column("license", sa.String(120), nullable=False, server_default=""),
        sa.Column("current_file_id", sa.String(40), sa.ForeignKey("files.id"), nullable=True),
        sa.Column("created_by", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_platform_skills_live_id", "platform_skills", ["deleted_at", "id"])
    op.create_index("ix_platform_skills_current_file", "platform_skills", ["current_file_id"])
    op.create_table(
        "user_platform_skills",
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("skill_id", sa.String(40), sa.ForeignKey("platform_skills.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("custom_lineage_id", sa.String(40), nullable=True),
        sa.Column("base_file_id", sa.String(40), sa.ForeignKey("files.id"), nullable=True),
    )
    op.create_index("ix_user_platform_skills_skill_user", "user_platform_skills", ["skill_id", "user_id"])
    op.create_index("ix_user_platform_skills_custom", "user_platform_skills", ["custom_lineage_id"])
    op.create_index("ix_user_platform_skills_base_file", "user_platform_skills", ["base_file_id"])
    op.create_index("ix_skill_drafts_base_file", "skill_drafts", ["base_file_id"])


def downgrade():
    op.drop_index("ix_skill_drafts_base_file", table_name="skill_drafts")
    op.drop_table("user_platform_skills")
    op.drop_table("platform_skills")
