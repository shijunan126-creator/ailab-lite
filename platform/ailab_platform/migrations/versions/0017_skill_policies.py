"""Skill governance: admin policies, personal-skill approvals, and a sparse index for the review queue.

The predicate is written out here, not imported from models, so this revision keeps its meaning if models change.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "skill_policies",
        sa.Column("scope", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
    )
    op.create_table(
        "skill_approvals",
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
    )

    op.create_index(
        "ix_files_skill_review",
        "files",
        ["id"],
        sqlite_where=sa.text(
            "json_extract(meta, '$.skill.name') IS NOT NULL AND status = 'ready' "
            "AND is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"
        ),
        postgresql_where=sa.text(
            "(meta -> 'skill' ->> 'name') IS NOT NULL AND status = 'ready' AND is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"
        ),
    )


def downgrade():
    op.drop_index("ix_files_skill_review", table_name="files")
    op.drop_table("skill_approvals")
    op.drop_table("skill_policies")
