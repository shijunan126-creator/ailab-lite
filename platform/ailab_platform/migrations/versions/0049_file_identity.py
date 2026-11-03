"""Durable upload operations and sandbox output identities."""

import sqlalchemy as sa
from alembic import op

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "upload_receipts",
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("client_upload_id", sa.String(100), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("file_id", sa.String(40), nullable=False),
        sa.Column("reservation_id", sa.String(40), nullable=True),
        sa.Column("reused", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_upload_receipts_file_id", "upload_receipts", ["file_id"])
    op.create_index("ix_upload_receipts_reservation_id", "upload_receipts", ["reservation_id"])
    op.add_column("artifacts", sa.Column("output_key", sa.String(500), nullable=True))
    op.create_index("ix_artifacts_run_output", "artifacts", ["run_id", "output_key"])
    op.add_column("run_deliveries", sa.Column("fingerprint", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("run_deliveries", "fingerprint")
    op.drop_index("ix_artifacts_run_output", table_name="artifacts")
    op.drop_column("artifacts", "output_key")
    op.drop_table("upload_receipts")
