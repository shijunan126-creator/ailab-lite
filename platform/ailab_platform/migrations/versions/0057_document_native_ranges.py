"""Immutable native scopes without replacing the base document index."""

import sqlalchemy as sa
from alembic import op

revision = "0057"
down_revision = "0056"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_native_ranges",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("parser", sa.String(80), nullable=False),
        sa.Column("base_derivation_id", sa.String(40), sa.ForeignKey("file_derivations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("selector_key", sa.String(64), nullable=False),
        sa.Column("selector", sa.JSON(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("derivation_id", sa.String(40), sa.ForeignKey("file_derivations.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("file_id", "source_version", "parser", "base_derivation_id", "selector_key", name="uq_document_native_range"),
    )
    op.create_index("ix_document_native_ranges_order", "document_native_ranges", ["file_id", "source_version", "parser", "ordinal"])
    op.create_index("ix_document_native_ranges_derivation", "document_native_ranges", ["derivation_id", "page"])


def downgrade():
    op.drop_table("document_native_ranges")
