"""Allow bounded consecutive memory consolidation batches."""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("memory_dream_runs") as batch:
        batch.drop_constraint("uq_memory_dream_user_date", type_="unique")
        batch.add_column(sa.Column("report", sa.JSON(), nullable=False, server_default="{}"))
        batch.add_column(sa.Column("batch_index", sa.Integer(), nullable=False, server_default="1"))
    op.create_index("ix_memory_dream_user_date_trigger", "memory_dream_runs", ["user_id", "local_date", "trigger"])
    predicate = sa.text("status IN ('pending', 'running')")
    op.create_index(
        "uq_memory_dream_user_active", "memory_dream_runs", ["user_id"], unique=True, sqlite_where=predicate, postgresql_where=predicate
    )


def downgrade():
    op.drop_index("uq_memory_dream_user_active", table_name="memory_dream_runs")
    op.drop_index("ix_memory_dream_user_date_trigger", table_name="memory_dream_runs")
    with op.batch_alter_table("memory_dream_runs") as batch:
        batch.drop_column("batch_index")
        batch.drop_column("report")
        batch.create_unique_constraint("uq_memory_dream_user_date", ["user_id", "local_date"])
