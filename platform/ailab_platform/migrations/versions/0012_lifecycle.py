"""Bounded lifecycle queries and durable object cleanup intents.

The usage ledger stops referencing runs: purging a deleted conversation removes its runs, but what those runs
cost stays on the books (quota windows, admin reports, reconciliation).
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0009"
branch_labels = None
depends_on = None


# SQLite reflects the FK without a name; give it one so batch mode can drop or recreate it.
NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _usage_run_fk(bind) -> str | None:
    for fk in sa.inspect(bind).get_foreign_keys("model_usage"):
        if fk["constrained_columns"] == ["run_id"]:
            return fk["name"] or "fk_model_usage_run_id_runs"
    return None


def upgrade():
    bind = op.get_bind()
    name = _usage_run_fk(bind)
    if name:
        # model_usage is referenced by no other table, so a batch rebuild on SQLite is safe here
        with op.batch_alter_table("model_usage", naming_convention=NAMING) as batch:
            batch.drop_constraint(name, type_="foreignkey")
    op.create_table(
        "lifecycle_deletions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(500), nullable=False, unique=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("is_prefix", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_files_status_created", "files", ["status", "created_at", "id"])
    op.create_index("ix_runs_conversation_id_id", "runs", ["conversation_id", "id"])
    op.create_index("ix_conversations_deleted", "conversations", ["deleted_at", "id"])
    op.create_index("ix_runs_finished", "runs", ["status", "finished_at", "id"])
    op.create_index("ix_run_events_created", "run_events", ["created_at", "id"])


def downgrade():
    op.drop_index("ix_run_events_created", "run_events")
    op.drop_index("ix_runs_finished", "runs")
    op.drop_index("ix_conversations_deleted", "conversations")
    op.drop_index("ix_runs_conversation_id_id", "runs")
    op.drop_index("ix_files_status_created", "files")
    op.drop_table("lifecycle_deletions")
    # Usage of runs purged meanwhile has no run to point at; it cannot regain the constraint.
    if op.get_bind().execute(sa.text("SELECT 1 FROM model_usage u LEFT JOIN runs r ON r.id = u.run_id WHERE r.id IS NULL LIMIT 1")).first():
        return
    with op.batch_alter_table("model_usage", naming_convention=NAMING) as batch:
        batch.create_foreign_key("fk_model_usage_run_id_runs", "runs", ["run_id"], ["id"])
