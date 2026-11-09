"""Drop unused entity projections; retain item columns to avoid SQLite FK table rebuilds."""

import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index("uq_memory_items_hash", table_name="memory_items")
    op.create_index(
        "uq_memory_items_hash",
        "memory_items",
        ["user_id", sa.text("coalesce(project_id, '')"), "kind", "content_hash"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("memory_entity_aliases")
    op.drop_table("memory_entities")
    op.execute("UPDATE memory_items SET origin = 'edited' WHERE origin = 'manual'")


def downgrade():
    op.drop_index("uq_memory_items_hash", table_name="memory_items")
    # The older schema allowed one kind per content; retain the oldest active row.
    op.execute(
        "UPDATE memory_items SET status = 'closed' WHERE status = 'active' AND id NOT IN "
        "(SELECT min(id) FROM memory_items WHERE status = 'active' GROUP BY user_id, coalesce(project_id, ''), content_hash)"
    )
    op.create_index(
        "uq_memory_items_hash",
        "memory_items",
        ["user_id", sa.text("coalesce(project_id, '')"), "content_hash"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "memory_entities",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        sa.Column("canonical", sa.String(120), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("conversations", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_memory_entities_project_id", "memory_entities", ["project_id"])
    op.create_index(
        "uq_memory_entities_scope", "memory_entities", ["user_id", sa.text("coalesce(project_id, '')"), "canonical"], unique=True
    )
    op.create_table(
        "memory_entity_aliases",
        sa.Column("entity_id", sa.String(40), sa.ForeignKey("memory_entities.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("name", sa.String(120), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    )
    op.create_index("ix_memory_alias_lookup", "memory_entity_aliases", ["user_id", "name"])
