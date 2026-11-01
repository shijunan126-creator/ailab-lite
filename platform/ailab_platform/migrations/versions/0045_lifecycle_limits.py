"""Frozen asset retention and schedule guardrails."""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("scheduled_tasks", sa.Column("ends_on", sa.Date(), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("paused_reason", sa.String(20), nullable=True))
    op.add_column("scheduled_tasks", sa.Column("recent_run_ids", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("scheduled_tasks", sa.Column("failure_streak", sa.Integer(), nullable=False, server_default="0"))
    op.create_table(
        "run_asset_refs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.UniqueConstraint("run_id", "file_id", "kind", name="uq_run_asset_refs_run_file_kind"),
    )
    op.create_index("ix_run_asset_refs_file", "run_asset_refs", ["file_id"])
    op.create_index("ix_files_asset_gc", "files", ["purpose", "purged_at", "created_at", "id"])
    runs = sa.table("runs", sa.column("id", sa.String), sa.column("options", sa.JSON), sa.column("runtime_meta", sa.JSON))
    refs = sa.table("run_asset_refs", *[sa.column(c, sa.String) for c in ("id", "run_id", "file_id", "kind")])
    conn = op.get_bind()
    after = ""
    while rows := conn.execute(sa.select(runs).where(runs.c.id > after).order_by(runs.c.id).limit(500)).mappings().all():
        batch = []
        for row in rows:
            assets = set()
            template = (row["options"] or {}).get("template") or {}
            if template.get("file_id"):
                assets.add((template["file_id"], "template"))
            for fid in (((row["runtime_meta"] or {}).get("brand") or {}).get("assets") or {}).values():
                if fid:
                    assets.add((fid, "brand"))
            batch.extend(dict(id="rar_" + uuid.uuid4().hex[:20], run_id=row["id"], file_id=fid, kind=kind) for fid, kind in assets)
        if batch:
            conn.execute(sa.insert(refs), batch)
        after = rows[-1]["id"]

    # Queued prompts already contain frozen templates but do not have their own Run yet.
    queued = sa.table("queued_messages", sa.column("id", sa.String), sa.column("after_run_id", sa.String), sa.column("options", sa.JSON))
    after = ""
    while (
        rows := conn.execute(
            sa.select(queued).where(queued.c.id > after, queued.c.after_run_id.in_(sa.select(runs.c.id))).order_by(queued.c.id).limit(500)
        )
        .mappings()
        .all()
    ):
        for row in rows:
            fid = ((row["options"] or {}).get("template") or {}).get("file_id")
            if fid and not conn.scalar(
                sa.select(refs.c.id).where(refs.c.run_id == row["after_run_id"], refs.c.file_id == fid, refs.c.kind == "template")
            ):
                conn.execute(
                    sa.insert(refs), dict(id="rar_" + uuid.uuid4().hex[:20], run_id=row["after_run_id"], file_id=fid, kind="template")
                )
        after = rows[-1]["id"]


def downgrade():
    op.drop_index("ix_files_asset_gc", table_name="files")
    op.drop_table("run_asset_refs")
    for column in ("failure_streak", "recent_run_ids", "paused_reason", "ends_on"):
        op.drop_column("scheduled_tasks", column)
