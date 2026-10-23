"""File-job accounting; ledger records have no resource foreign keys."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("model_usage", "usage_reservations"):
        op.add_column(table, sa.Column("subject", sa.String(20), nullable=False, server_default="run"))
        op.add_column(table, sa.Column("file_id", sa.String(40), nullable=True))
        # These leaf tables are not referenced by any foreign key. SQLite requires
        # a rebuild to relax NOT NULL; never rebuild runs/files/users here.
        with op.batch_alter_table(table) as batch:
            batch.alter_column("run_id", existing_type=sa.String(40), nullable=True)
        op.get_bind().execute(sa.text(f"UPDATE {table} SET subject = 'file_job', file_id = run_id, run_id = NULL WHERE mode = 'ocr'"))


def downgrade():
    conn = op.get_bind()
    # Downgrade preserves billed amounts and owner, using the historical opaque
    # run-id field to retain file provenance for older binaries.
    for table in ("model_usage", "usage_reservations"):
        conn.execute(sa.text(f"UPDATE {table} SET run_id = file_id WHERE run_id IS NULL"))
        with op.batch_alter_table(table) as batch:
            batch.alter_column("run_id", existing_type=sa.String(40), nullable=False)
        op.drop_column(table, "file_id")
        op.drop_column(table, "subject")
