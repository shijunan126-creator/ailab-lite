"""Separate skill storage and reserve sibling names without losing historical objects."""

import hashlib
import json
import unicodedata
from pathlib import PurePath

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def _key(value):
    return hashlib.sha256(" ".join(unicodedata.normalize("NFKC", value).split()).casefold().encode()).hexdigest()


def _number(value, n, limit):
    suffix = PurePath(value).suffix[:40]
    tail = f" ({n}){suffix}"
    return (value[: -len(suffix)] if suffix else value)[: limit - len(tail)] + tail


def upgrade():
    op.add_column("projects", sa.Column("name_key", sa.String(64), nullable=False, server_default=""))
    op.add_column("files", sa.Column("name_key", sa.String(64), nullable=False, server_default=""))
    op.add_column("files", sa.Column("purpose", sa.String(16), nullable=False, server_default="library"))
    op.create_index("_backfill_project_names", "projects", ["user_id", "name_key"])
    op.create_index("_backfill_file_names", "files", ["user_id", "name_key"])
    conn = op.get_bind()
    # Keyset pages bound memory even when upgrading a large library.
    for table, label, limit in [("projects", "name", 120), ("files", "filename", 200)]:
        after = ""
        ordinal = 1
        while rows := conn.execute(sa.text(f"SELECT * FROM {table} WHERE id > :id ORDER BY id LIMIT 200"), {"id": after}).mappings().all():
            for row in rows:
                value = row[label]
                purpose = "library"
                if table == "files":
                    meta = row["meta"] if isinstance(row["meta"], dict) else json.loads(row["meta"] or "{}")
                    purpose = "skill" if meta.get("skill") or meta.get("skill_error") else "library"
                live = table == "projects" or (row["is_head"] and row["deleted_at"] is None and row["purged_at"] is None)
                candidate, number = value, ordinal
                ordinal += 1
                while live:
                    params = {"user": row["user_id"], "key": _key(candidate), "id": row["id"]}
                    extra = ""
                    if table == "files":
                        params.update(purpose=purpose, project=row["project_id"] or "")
                        extra = (
                            " AND purpose=:purpose AND coalesce(project_id,'')=:project"
                            " AND is_head=1 AND deleted_at IS NULL AND purged_at IS NULL"
                        )
                    found = conn.execute(
                        sa.text(f"SELECT id FROM {table} WHERE user_id=:user AND name_key=:key AND id<>:id{extra} LIMIT 1"), params
                    ).first()
                    if not found:
                        break
                    number += 1
                    candidate = _number(value, number, limit)
                params = {"name": candidate, "key": _key(candidate), "id": row["id"], "purpose": purpose}
                conn.execute(
                    sa.text(
                        f"UPDATE {table} SET {label}=:name, name_key=:key"
                        + (", purpose=:purpose" if table == "files" else "")
                        + " WHERE id=:id"
                    ),
                    params,
                )
            after = rows[-1]["id"]
    op.create_index("uq_projects_user_name", "projects", ["user_id", "name_key"], unique=True)
    op.create_index(
        "ix_files_folder_listing", "files", ["user_id", "purpose", "is_head", "deleted_at", "purged_at", "project_id", "created_at", "id"]
    )
    op.create_index(
        "uq_files_folder_name",
        "files",
        ["user_id", "purpose", sa.text("coalesce(project_id, '')"), "name_key"],
        unique=True,
        sqlite_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
        postgresql_where=sa.text("is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL"),
    )

    op.drop_index("_backfill_file_names", table_name="files")
    op.drop_index("_backfill_project_names", table_name="projects")


def downgrade():
    op.drop_index("uq_files_folder_name", table_name="files")
    op.drop_index("ix_files_folder_listing", table_name="files")
    op.drop_index("uq_projects_user_name", table_name="projects")
    op.drop_column("files", "purpose")
    op.drop_column("files", "name_key")
    op.drop_column("projects", "name_key")
