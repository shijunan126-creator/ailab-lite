"""Durable document execution slots, identities and absolute deadlines."""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_derived_assets_kind_state", "derived_assets", ["kind", "state", "key"])
    op.create_table(
        "document_lanes", sa.Column("name", sa.String(20), primary_key=True), sa.Column("last_user_id", sa.String(40), nullable=True)
    )
    op.execute(sa.text("INSERT INTO document_lanes (name) VALUES ('render'), ('ocr')"))
    op.create_table(
        "document_operations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("file_id", sa.String(40), nullable=True),
        sa.Column("identity", sa.String(64), nullable=False),
        sa.Column("lane", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("binding", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claim_token", sa.String(40), nullable=True),
        sa.Column("executor_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("execution_deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_token", sa.String(40), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("run_id", "identity", name="uq_document_operation_identity"),
    )
    for name, columns in (
        ("ix_document_operations_lane_queue", ["lane", "status", "created_at", "id"]),
        ("ix_document_operations_user_lane", ["user_id", "lane", "status", "created_at"]),
        ("ix_document_operations_run", ["run_id", "status"]),
        ("ix_document_operations_lease", ["status", "lease_until"]),
    ):
        op.create_index(name, "document_operations", columns)


def downgrade():
    op.drop_index("ix_derived_assets_kind_state", table_name="derived_assets")
    op.drop_table("document_operations")
    op.drop_table("document_lanes")
