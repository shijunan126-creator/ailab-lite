"""Remove obsolete xlsx disable settings; downgrade cannot restore deleted choices."""

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    users = sa.table("users", sa.column("id", sa.String), sa.column("settings", sa.JSON))
    for user_id, settings in conn.execute(sa.select(users.c.id, users.c.settings)):
        if isinstance(settings, dict) and "xlsx" in (settings.get("disabled_skills") or []):
            updated = {**settings, "disabled_skills": [name for name in settings["disabled_skills"] if name != "xlsx"]}
            conn.execute(users.update().where(users.c.id == user_id).values(settings=updated))
    conn.execute(sa.text("DELETE FROM skill_policies WHERE name = :name"), {"name": "xlsx"})


def downgrade():
    # The deleted preference and policies cannot be reconstructed.
    pass
