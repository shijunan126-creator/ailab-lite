"""Derived memory summaries and per-project visibility."""

import sqlalchemy as sa
from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


def _convert(downgrade=False):
    conn = op.get_bind()
    users = sa.table("users", sa.column("id", sa.String), sa.column("settings", sa.JSON))
    projects = sa.table("projects", sa.column("user_id", sa.String), sa.column("memory_mode", sa.String))
    after = ""
    while rows := conn.execute(sa.select(users).where(users.c.id > after).order_by(users.c.id).limit(200)).mappings().all():
        for row in rows:
            settings = dict(row["settings"] or {})
            memory = dict(settings.get("memory") or {})
            if downgrade:
                memory["project_only"] = bool(
                    conn.scalar(
                        sa.select(projects.c.user_id).where(projects.c.user_id == row["id"], projects.c.memory_mode == "isolated").limit(1)
                    )
                )
            else:
                if memory.pop("project_only", False):
                    conn.execute(projects.update().where(projects.c.user_id == row["id"]).values(memory_mode="isolated"))
            settings["memory"] = memory
            conn.execute(users.update().where(users.c.id == row["id"]).values(settings=settings))
        after = rows[-1]["id"]


def upgrade():
    op.add_column("projects", sa.Column("memory_mode", sa.String(16), nullable=False, server_default="shared"))
    op.add_column("projects", sa.Column("memory_merge_requested_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memory_dream_runs", sa.Column("project_id", sa.String(40), nullable=True))
    op.add_column("memory_dream_runs", sa.Column("instruction", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_projects_memory_pool", "projects", ["user_id", "memory_mode", "id"])
    op.create_index("ix_projects_memory_merge", "projects", ["user_id", "memory_merge_requested_at"])
    op.create_table(
        "memory_summaries",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_digest", sa.String(64), nullable=False, server_default=""),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_memory_summaries_user", "memory_summaries", ["user_id"])
    op.create_index("ix_memory_summaries_project_id", "memory_summaries", ["project_id"])
    for name, columns, condition in (
        ("account", ["user_id"], "project_id IS NULL"),
        ("project", ["user_id", "project_id"], "project_id IS NOT NULL"),
    ):
        op.create_index(
            "uq_memory_summary_" + name,
            "memory_summaries",
            columns,
            unique=True,
            sqlite_where=sa.text(condition),
            postgresql_where=sa.text(condition),
        )
    _convert()


def downgrade():
    _convert(True)
    op.drop_table("memory_summaries")
    op.drop_index("ix_projects_memory_pool", table_name="projects")
    op.drop_index("ix_projects_memory_merge", table_name="projects")
    op.drop_column("memory_dream_runs", "instruction")
    op.drop_column("memory_dream_runs", "project_id")
    op.drop_column("projects", "memory_merge_requested_at")
    op.drop_column("projects", "memory_mode")
