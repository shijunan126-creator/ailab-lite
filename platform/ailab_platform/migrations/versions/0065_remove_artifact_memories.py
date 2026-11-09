"""Remove redundant delivery memories and their undo copies without tombstones."""

import sqlalchemy as sa
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade():
    db = op.get_bind()
    items = sa.table("memory_items", sa.column("user_id", sa.String), sa.column("kind", sa.String))
    users = sa.table("users", sa.column("id", sa.String), sa.column("settings", sa.JSON))
    affected = list(db.scalars(sa.select(items.c.user_id).where(items.c.kind == "artifact").distinct()))
    db.execute(items.delete().where(items.c.kind == "artifact"))
    for uid in affected:
        settings = dict(db.scalar(sa.select(users.c.settings).where(users.c.id == uid)) or {})
        memory = dict(settings.get("memory") or {})
        memory["revision"] = int(memory.get("revision", 0)) + 1
        settings["memory"] = memory
        db.execute(users.update().where(users.c.id == uid).values(settings=settings))
    dreams = sa.table("memory_dream_runs", sa.column("id", sa.String), sa.column("plan", sa.JSON), sa.column("change_set", sa.JSON))
    for row in db.execute(sa.select(dreams)).mappings():
        plan, changes = dict(row["plan"] or {}), dict(row["change_set"] or {})
        if "changes" in plan:
            plan["changes"] = [e for e in plan["changes"] if e.get("kind") != "artifact"]
        if "items" in changes:
            changes["items"] = [
                e for e in changes["items"] if not any((e.get(side) or {}).get("kind") == "artifact" for side in ("before", "after"))
            ]
        if plan != row["plan"] or changes != row["change_set"]:
            db.execute(dreams.update().where(dreams.c.id == row["id"]).values(plan=plan, change_set=changes))


def downgrade():
    # Housekeeping deletes redundant data; library deliveries remain authoritative.
    pass
