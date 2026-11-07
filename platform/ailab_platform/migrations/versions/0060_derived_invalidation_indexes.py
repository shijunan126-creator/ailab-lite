"""Bound cache invalidation to the actual deleted object on both databases."""

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_file_derivations_manifest", "file_derivations", ["manifest_key"]),
    ("ix_document_blocks_text_key", "document_blocks", ["text_key", "derivation_id"]),
    ("ix_rendered_pages_key", "rendered_pages", ["key"]),
    ("ix_ocr_page_results_key", "ocr_page_results", ["text_key"]),
    ("ix_document_ocr_units_key", "document_ocr_units", ["text_key"]),
)


def upgrade():
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)


def downgrade():
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
