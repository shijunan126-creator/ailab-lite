"""Conversation paths, pinning and independently identifiable main model usage."""

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runs", sa.Column("parent_run_id", sa.String(40), nullable=True))
    op.add_column("runs", sa.Column("edited_from", sa.String(40), nullable=True))
    op.add_column("conversations", sa.Column("active_run_id", sa.String(40), nullable=True))
    op.add_column("conversations", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    for table in ("model_usage", "usage_reservations"):
        # Historical quiet calls were not marked; unknown stays unknown, never guess a composer meter.
        op.add_column(table, sa.Column("quiet", sa.Boolean(), nullable=True))
    op.create_index("ix_runs_conversation_parent", "runs", ["conversation_id", "parent_run_id", "created_at", "id"])
    op.create_index("ix_conversations_user_pinned", "conversations", ["user_id", "pinned_at", "updated_at"])
    op.create_index("ix_model_usage_context", "model_usage", ["run_id", "quiet", "status", "created_at", "id"])
    conn = op.get_bind()
    after = ""
    while True:
        conversations = (
            conn.execute(sa.text("SELECT id FROM conversations WHERE id > :after ORDER BY id LIMIT 100"), {"after": after}).scalars().all()
        )
        if not conversations:
            break
        for cid in conversations:
            rows = conn.execute(
                sa.text("SELECT id, retry_of, superseded_by_run_id FROM runs WHERE conversation_id=:cid ORDER BY created_at,id"),
                {"cid": cid},
            ).all()
            replaced = {r.superseded_by_run_id: r.id for r in rows if r.superseded_by_run_id}
            parents, leaf = {}, None
            for row in rows:
                original = row.retry_of or replaced.get(row.id)
                parent = parents[original] if original in parents else leaf
                parents[row.id] = parent
                conn.execute(sa.text("UPDATE runs SET parent_run_id=:parent WHERE id=:id"), {"parent": parent, "id": row.id})
                # Retried rows occupy their original slot even when the old row is superseded.
                leaf = row.id
            conn.execute(sa.text("UPDATE conversations SET active_run_id=:leaf WHERE id=:id"), {"leaf": leaf, "id": cid})
        after = conversations[-1]


def downgrade():
    op.drop_index("ix_model_usage_context", table_name="model_usage")
    op.drop_index("ix_conversations_user_pinned", table_name="conversations")
    op.drop_index("ix_runs_conversation_parent", table_name="runs")
    for table in ("model_usage", "usage_reservations"):
        op.drop_column(table, "quiet")
    op.drop_column("conversations", "pinned_at")
    op.drop_column("conversations", "active_run_id")
    op.drop_column("runs", "edited_from")
    op.drop_column("runs", "parent_run_id")
