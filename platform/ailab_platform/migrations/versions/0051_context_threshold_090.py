"""Migrate only the old persisted default; record ownership for reversible downgrade.

A migration-owned setting records the exact migrated value. Downgrade only
reverses that value if it is still unchanged, never an independently stored 0.90.
"""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None
_MARKER = "migration_0051_context"


def _table():
    return sa.table("platform_settings", sa.column("key", sa.String()), sa.column("value", sa.JSON()))


def upgrade():
    table = _table()
    conn = op.get_bind()
    value = conn.scalar(sa.select(table.c.value).where(table.c.key == "context"))
    if isinstance(value, dict) and value.get("threshold_ratio") == 0.85:
        updated = {**value, "threshold_ratio": 0.90}
        conn.execute(table.insert().values(key=_MARKER, value=updated))
        conn.execute(table.update().where(table.c.key == "context").values(value=updated))


def downgrade():
    table = _table()
    conn = op.get_bind()
    ours = conn.scalar(sa.select(table.c.value).where(table.c.key == _MARKER))
    current = conn.scalar(sa.select(table.c.value).where(table.c.key == "context"))
    if ours is not None and current == ours:
        conn.execute(table.update().where(table.c.key == "context").values(value={**current, "threshold_ratio": 0.85}))
    conn.execute(table.delete().where(table.c.key == _MARKER))
