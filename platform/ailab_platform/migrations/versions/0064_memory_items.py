"""Memory v2 relational items and entities, including legacy data conversion."""

import hashlib
import re
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "memory_items",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id", ondelete="CASCADE")),
        *[
            sa.Column(k, sa.String(n), nullable=False)
            for k, n in (("kind", 20), ("origin", 20), ("tier", 20), ("page", 120), ("content_hash", 64))
        ],
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("entities", sa.JSON(), nullable=False),
        sa.Column("source_run_ids", sa.JSON(), nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=False),
        *[
            sa.Column(k, sa.DateTime(timezone=True), nullable=False)
            for k in ("observed_at", "valid_from", "reinforced_at", "created_at", "updated_at")
        ],
        *[sa.Column(k, sa.DateTime(timezone=True)) for k in ("valid_to", "expires_at")],
        sa.Column("superseded_by", sa.String(40)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("support_conversations", sa.Integer(), nullable=False),
        sa.Column("support_dates", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
    )
    op.create_index("ix_memory_items_project_id", "memory_items", ["project_id"])
    op.create_index("ix_memory_items_scope", "memory_items", ["user_id", "project_id", "status", "tier"])
    op.create_index("ix_memory_items_page", "memory_items", ["user_id", "project_id", "page", "updated_at", "id"])
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
    db = op.get_bind()
    users = sa.table("users", sa.column("id", sa.String()), sa.column("settings", sa.JSON()))
    items = sa.Table("memory_items", sa.MetaData(), autoload_with=db)
    now = datetime.now(UTC)
    for user in db.execute(sa.select(users)).mappings():
        settings = dict(user["settings"] or {})
        memory = dict(settings.get("memory") or {})
        seen = set()
        for e in memory.pop("entries", []) or []:
            content = e.get("content", "")
            digest = hashlib.sha256(re.sub(r"[\W_]+", " ", content.casefold()).strip().encode()).hexdigest()
            key = (e.get("project_id"), digest)
            if key in seen:
                continue
            seen.add(key)

            def at(k):
                return datetime.fromisoformat(e[k]) if e.get(k) else None

            kind = "preference" if e["kind"] == "user" else "fact"
            db.execute(
                items.insert().values(
                    id=e["id"],
                    user_id=user["id"],
                    project_id=e.get("project_id"),
                    kind=kind,
                    origin="explicit" if e.get("source") == "nightly" else "manual",
                    tier="core",
                    page="偏好" if kind == "preference" else "工作与身份",
                    content=content,
                    entities=e.get("entities") or [],
                    source_run_ids=e.get("evidence_run_ids") or [],
                    source_quote=e.get("source_quote") or "",
                    observed_at=at("updated_at") or now,
                    valid_from=at("valid_from") or now,
                    valid_to=at("invalid_at"),
                    expires_at=None,
                    superseded_by=e.get("superseded_by"),
                    status="suppressed" if e.get("suppressed") else "closed" if e.get("invalid_at") else e.get("status", "active"),
                    confidence=e.get("confidence", 1),
                    support_conversations=1,
                    support_dates=1,
                    reinforced_at=at("updated_at") or now,
                    content_hash=digest,
                    created_at=at("updated_at") or now,
                    updated_at=at("updated_at") or now,
                    metadata={k: e[k] for k in ("source", "evidence_episode_ids", "evidence_deleted", "last_used_at") if k in e},
                )
            )
        settings["memory"] = memory
        db.execute(users.update().where(users.c.id == user["id"]).values(settings=settings))

    work = sa.table(
        "memory_work_items",
        sa.column("state", sa.String()),
        sa.column("candidates", sa.JSON()),
        sa.column("source_offset", sa.Integer()),
        sa.column("source_end", sa.Integer()),
    )
    for row in db.execute(sa.select(sa.Table("memory_work_items", sa.MetaData(), autoload_with=db))).mappings():
        if row["state"] == "done" and not row["candidates"]:
            db.execute(
                work.update().where(sa.literal_column("run_id") == row["run_id"]).values(state="backfill", source_offset=0, source_end=0)
            )


def downgrade():
    db = op.get_bind()
    users = sa.table("users", sa.column("id", sa.String()), sa.column("settings", sa.JSON()))
    items = sa.Table("memory_items", sa.MetaData(), autoload_with=db)
    for user in db.execute(sa.select(users)).mappings():
        settings = dict(user["settings"] or {})
        entries = []
        for e in db.execute(sa.select(items).where(items.c.user_id == user["id"])).mappings():
            entries.append(
                {
                    **e["metadata"],
                    "id": e["id"],
                    "kind": "user" if e["kind"] == "preference" else "memory",
                    "content": e["content"],
                    "project_id": e["project_id"],
                    "evidence_run_ids": e["source_run_ids"],
                    "source_quote": e["source_quote"],
                    "entities": e["entities"],
                    "confidence": e["confidence"],
                    "status": "review" if e["status"] == "review" else "active",
                    "suppressed": e["status"] == "suppressed",
                    **{
                        k: (e[v].isoformat() if e[v] else None)
                        for k, v in (("valid_from", "valid_from"), ("invalid_at", "valid_to"), ("updated_at", "updated_at"))
                    },
                }
            )
        settings["memory"] = {**(settings.get("memory") or {}), "entries": entries}
        db.execute(users.update().where(users.c.id == user["id"]).values(settings=settings))
    op.drop_table("memory_entities")
    op.drop_table("memory_items")
