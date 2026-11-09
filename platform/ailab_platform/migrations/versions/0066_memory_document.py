"""Drop unused entity projections; retain item columns to avoid SQLite FK table rebuilds."""

import sqlalchemy as sa
from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memory_episodes", sa.Column("context_run_id", sa.String(40), nullable=True))
    op.create_index("ix_memory_episodes_context_run_id", "memory_episodes", ["context_run_id"])
    op.drop_index("ix_memory_items_scope", table_name="memory_items")
    op.drop_index("ix_memory_items_page", table_name="memory_items")
    op.drop_column("memory_items", "tier")
    op.drop_column("memory_items", "page")
    for name in ("entities", "support_conversations", "support_dates"):
        op.drop_column("memory_items", name)
    op.create_index("ix_memory_items_scope", "memory_items", ["user_id", "project_id", "status"])
    op.create_index("ix_memory_items_retention", "memory_items", ["status", "valid_to", "id"])
    op.drop_index("uq_memory_items_hash", table_name="memory_items")
    op.create_index(
        "uq_memory_items_hash",
        "memory_items",
        ["user_id", sa.text("coalesce(project_id, '')"), "kind", "content_hash"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index("ix_memory_items_content", "memory_items", ["user_id", "content_hash"])
    op.drop_table("memory_entity_aliases")
    op.drop_table("memory_entities")
    op.execute("UPDATE memory_items SET valid_to = updated_at WHERE status = 'closed' AND valid_to IS NULL")
    op.execute("UPDATE memory_items SET origin = 'edited' WHERE origin = 'manual'")


def downgrade():
    op.drop_index("ix_memory_episodes_context_run_id", table_name="memory_episodes")
    op.drop_column("memory_episodes", "context_run_id")
    op.add_column("memory_items", sa.Column("entities", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("memory_items", sa.Column("support_conversations", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("memory_items", sa.Column("support_dates", sa.Integer(), nullable=False, server_default="1"))
    op.drop_index("ix_memory_items_content", table_name="memory_items")
    op.drop_index("ix_memory_items_scope", table_name="memory_items")
    op.drop_index("ix_memory_items_retention", table_name="memory_items")
    op.add_column("memory_items", sa.Column("tier", sa.String(20), nullable=False, server_default="archive"))
    op.add_column("memory_items", sa.Column("page", sa.String(120), nullable=False, server_default="工作与身份"))
    op.create_index("ix_memory_items_scope", "memory_items", ["user_id", "project_id", "status", "tier"])
    op.create_index("ix_memory_items_page", "memory_items", ["user_id", "project_id", "page", "updated_at", "id"])
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
