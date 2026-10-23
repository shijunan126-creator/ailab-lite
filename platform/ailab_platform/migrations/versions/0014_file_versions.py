"""Immutable file lineages and thirty-day trash.

Revision ID: 0014
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Plain ADD COLUMN preserves inbound artifacts and file parent references on SQLite.
    op.add_column("files", sa.Column("lineage_id", sa.String(40), nullable=False, server_default=""))
    op.add_column("files", sa.Column("is_head", sa.Integer(), nullable=False, server_default="1"))
    for name in ("deleted_at", "purge_at", "purged_at"):
        op.add_column("files", sa.Column(name, sa.DateTime(timezone=True), nullable=True))
    op.add_column("files", sa.Column("trashed_from_project_id", sa.String(40), nullable=True))
    conn = op.get_bind()
    rows = {r.id: r for r in conn.execute(sa.text("SELECT id, user_id, parent_id, version, created_at FROM files"))}
    roots = {}
    groups = {}
    for fid in rows:
        chain, seen, current = [], set(), fid
        while current not in roots:
            if current in seen or current not in rows or rows[current].user_id != rows[fid].user_id:
                raise RuntimeError("invalid file parent chain; repair before migration")
            chain.append(current)
            seen.add(current)
            parent = rows[current].parent_id
            if parent is None:
                roots[current] = current
                break
            if parent not in rows or rows[parent].user_id != rows[fid].user_id:
                raise RuntimeError("invalid file parent chain; repair before migration")
            current = parent
        for child in chain:
            roots[child] = roots[current]
        groups.setdefault(roots[fid], []).append(rows[fid])
    updates = []
    for lineage, versions in groups.items():
        versions.sort(key=lambda r: (r.version, r.created_at, r.id))
        # Old upload APIs allowed branching from the same parent with duplicate numbers.
        # Renumber that ambiguous history once, without changing any file id or reference.
        renumber = len({r.version for r in versions}) != len(versions)
        for number, row in enumerate(versions, 1):
            updates.append(
                {"fid": row.id, "lineage": lineage, "head": int(row is versions[-1]), "version": number if renumber else row.version}
            )
    if updates:
        conn.execute(sa.text("UPDATE files SET lineage_id=:lineage, is_head=:head, version=:version WHERE id=:fid"), updates)
    op.create_index("ix_files_parent_id", "files", ["parent_id"])
    op.create_index("uq_files_lineage_version", "files", ["lineage_id", "version"], unique=True)
    op.create_index(
        "uq_files_lineage_head",
        "files",
        ["lineage_id"],
        sqlite_where=sa.text("is_head = 1"),
        postgresql_where=sa.text("is_head = 1"),
        unique=True,
    )
    op.create_index("ix_files_lineage_retained", "files", ["lineage_id", "purged_at", "version"])
    op.create_index(
        "ix_files_live_created",
        "files",
        ["user_id", "created_at", "id", "is_head", "deleted_at", "purged_at"],
        sqlite_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
        postgresql_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
    )
    op.create_index(
        "ix_files_live_project",
        "files",
        ["user_id", "project_id", "created_at", "id", "is_head", "deleted_at", "purged_at"],
        sqlite_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
        postgresql_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
    )
    op.create_index(
        "ix_files_live_starred",
        "files",
        ["user_id", "starred_at", "id", "is_head", "deleted_at", "purged_at"],
        sqlite_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL AND starred_at IS NOT NULL"),
        postgresql_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL AND starred_at IS NOT NULL"),
    )
    op.create_index(
        "ix_files_live_images",
        "files",
        ["user_id", "created_at", "id", "is_head", "content_type", "deleted_at", "purged_at"],
        sqlite_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL AND content_type LIKE 'image/%'"),
        postgresql_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL AND content_type LIKE 'image/%'"),
    )
    op.create_index(
        "ix_files_trash",
        "files",
        ["user_id", "deleted_at", "id"],
        sqlite_where=sa.text("is_head = 1 AND deleted_at IS NOT NULL"),
        postgresql_where=sa.text("is_head = 1 AND deleted_at IS NOT NULL"),
    )
    op.create_index(
        "ix_files_purge_due",
        "files",
        ["purge_at", "user_id", "lineage_id"],
        sqlite_where=sa.text("is_head = 1 AND deleted_at IS NOT NULL"),
        postgresql_where=sa.text("is_head = 1 AND deleted_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_files_purge_due", table_name="files")
    op.drop_index("ix_files_trash", table_name="files")
    op.drop_index("ix_files_live_images", table_name="files")
    op.drop_index("ix_files_live_starred", table_name="files")
    op.drop_index("ix_files_live_project", table_name="files")
    op.drop_index("ix_files_live_created", table_name="files")
    op.drop_index("ix_files_lineage_retained", table_name="files")
    op.drop_index("uq_files_lineage_head", table_name="files")
    op.drop_index("uq_files_lineage_version", table_name="files")
    op.drop_index("ix_files_parent_id", table_name="files")
    for name in ("trashed_from_project_id", "purged_at", "purge_at", "deleted_at", "is_head", "lineage_id"):
        op.drop_column("files", name)
