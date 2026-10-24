"""PPT templates and promotion of the legacy brand PPT slot."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import PurePath

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    from ailab_platform.config import settings

    table = op.create_table(
        "templates",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.String(40), sa.ForeignKey("users.id")),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id"), nullable=False),
        sa.Column("created_by", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind = 'pptx'", name="ck_templates_kind"),
        sa.CheckConstraint(
            "(scope = 'company' AND owner_id IS NULL) OR (scope = 'personal' AND owner_id IS NOT NULL)", name="ck_templates_scope_owner"
        ),
    )
    for scope, columns in [("company", ["kind", "created_at", "id"]), ("personal", ["owner_id", "kind", "created_at", "id"])]:
        predicate = sa.text(f"deleted_at IS NULL AND scope = '{scope}'")
        op.create_index(f"ix_templates_{scope}", "templates", columns, sqlite_where=predicate, postgresql_where=predicate)
    op.create_index("ix_templates_file_id", "templates", ["file_id"])
    for name, columns in [
        ("ix_files_slides", ["user_id", "created_at", "id"]),
        ("ix_files_slides_folders", ["user_id", "project_id", "created_at", "id"]),
    ]:
        predicate = sa.text(
            "purpose = 'library' AND is_head = 1 AND deleted_at IS NULL AND purged_at IS NULL AND "
            "(lower(filename) LIKE '%.pptx' OR lower(filename) LIKE '%.potx')"
        )
        op.create_index(name, "files", columns, sqlite_where=predicate, postgresql_where=predicate)
    conn = op.get_bind()
    settings = sa.table("platform_settings", sa.column("key", sa.String), sa.column("value", sa.JSON))
    files = sa.table(
        "files",
        sa.column("id", sa.String),
        sa.column("filename", sa.String),
        sa.column("user_id", sa.String),
        sa.column("purpose", sa.String),
        sa.column("meta", sa.JSON),
        sa.column("status", sa.String),
    )
    value = conn.execute(sa.select(settings.c.value).where(settings.c.key == "brand")).scalar()
    if value and value.get("pptx_template_file_id"):
        file = conn.execute(sa.select(files).where(files.c.id == value["pptx_template_file_id"])).mappings().first()
        if file:
            now = datetime.now(UTC)
            conn.execute(
                table.insert().values(
                    id="tpl_" + uuid.uuid4().hex[:20],
                    kind="pptx",
                    scope="company",
                    name=PurePath(file["filename"]).stem[:60],
                    file_id=file["id"],
                    created_by=file["user_id"],
                    created_at=now,
                    updated_at=now,
                )
            )
            meta = {**(file["meta"] or {}), "template_kind": "pptx", "processing": "queued"}
            conn.execute(files.update().where(files.c.id == file["id"]).values(purpose="template", status="pending", meta=meta))
            # Reanalyse in Worker (never decode legacy bytes in the migration) and create the first thumbnail.
            outbox = sa.table(
                "outbox",
                sa.column("id", sa.String),
                sa.column("topic", sa.String),
                sa.column("payload", sa.JSON),
                sa.column("created_at", sa.DateTime(timezone=True)),
            )
            job_id = "tpl_migrate_" + uuid.uuid4().hex[:20]
            conn.execute(
                outbox.insert().values(
                    id=job_id,
                    topic=settings.file_queue,
                    created_at=now,
                    payload={
                        "job_id": job_id,
                        "schema_version": 1,
                        "kind": "file",
                        "file_id": file["id"],
                        "run_id": None,
                        "mode": "work",
                        "attempt": 1,
                        "deadline": (now + timedelta(seconds=settings.job_deadline_seconds)).isoformat(),
                        "enqueued_at": now.isoformat(),
                    },
                )
            )
        # Keep the old pointer for downgrade and frozen historical runs only; new API ignores it.


def downgrade():
    conn = op.get_bind()
    settings = sa.table("platform_settings", sa.column("key", sa.String), sa.column("value", sa.JSON))
    value = conn.execute(sa.select(settings.c.value).where(settings.c.key == "brand")).scalar()
    if value and value.get("pptx_template_file_id"):
        conn.execute(sa.text("UPDATE files SET purpose='brand',status='ready' WHERE id=:id"), {"id": value["pptx_template_file_id"]})
    op.drop_index("ix_files_slides_folders", table_name="files")
    op.drop_index("ix_files_slides", table_name="files")
    # Remove only migration-produced jobs; ordinary uploads survive rollback for a later upgrade.
    conn.execute(sa.text("DELETE FROM outbox WHERE id LIKE 'tpl_migrate_%'"))
    op.drop_table("templates")
