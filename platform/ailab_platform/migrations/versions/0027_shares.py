"""Internal read-only shares.

Revision ID: 0027
Revises: 0024
"""

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_users_display_name", "users", ["display_name"])
    op.create_table(
        "shares",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("conversation_id", sa.String(40), sa.ForeignKey("conversations.id", ondelete="SET NULL")),
        sa.Column("through_run_id", sa.String(40)),
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("audience", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    for name, columns in {
        "ix_shares_owner_created_id": ["owner_id", "created_at", "id"],
        "ix_shares_conversation_owner": ["conversation_id", "owner_id"],
        "ix_shares_file_owner": ["file_id", "owner_id"],
        "ix_shares_audience_created_id": ["audience", "created_at", "id"],
    }.items():
        op.create_index(name, "shares", columns)
    op.create_table(
        "share_recipients",
        sa.Column("share_id", sa.String(40), sa.ForeignKey("shares.id"), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), primary_key=True),
    )
    op.create_index("ix_share_recipients_user_share", "share_recipients", ["user_id", "share_id"])


def downgrade():
    op.drop_index("ix_users_display_name", table_name="users")
    op.drop_table("share_recipients")
    op.drop_table("shares")
