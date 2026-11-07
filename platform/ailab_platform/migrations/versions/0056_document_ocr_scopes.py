"""Shared file OCR operations and immutable page/region text caches."""

import sqlalchemy as sa
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("document_operations", sa.Column("job_id", sa.String(40), nullable=True))
    op.create_index("ix_document_operations_file_identity", "document_operations", ["file_id", "identity", "status"])
    op.create_table(
        "document_operation_consumers",
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("operation_id", sa.String(40), sa.ForeignKey("document_operations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_consumers_operation", "document_operation_consumers", ["operation_id", "deadline"])
    op.create_table(
        "document_ocr_units",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("selector_key", sa.String(64), nullable=False),
        sa.Column("selector", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("image_revision", sa.String(100), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("text_key", sa.String(600), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False),
        sa.Column("engine", sa.String(80), nullable=False),
        sa.Column("backend", sa.String(16), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("operation_id", sa.String(40), sa.ForeignKey("document_operations.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("file_id", "source_version", "fingerprint", "selector_key", name="uq_document_ocr_scope"),
    )
    for name, columns in (
        ("ix_document_ocr_units_order", ["file_id", "source_version", "fingerprint", "ordinal"]),
        ("ix_document_ocr_units_page", ["file_id", "source_version", "page", "ordinal"]),
        ("ix_document_ocr_units_operation", ["operation_id"]),
    ):
        op.create_index(name, "document_ocr_units", columns)


def downgrade():
    op.drop_table("document_ocr_units")
    op.drop_table("document_operation_consumers")
    op.drop_index("ix_document_operations_file_identity", table_name="document_operations")
    op.drop_column("document_operations", "job_id")
