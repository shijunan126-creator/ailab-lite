"""Vectors of finished turns for the dense channel of history recall."""

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_embeddings",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "model", name="uq_memory_embedding_run_model"),
    )
    op.create_index("ix_memory_embeddings_run_id", "memory_embeddings", ["run_id"])
    op.create_index("ix_memory_embedding_user_model", "memory_embeddings", ["user_id", "model"])


def downgrade() -> None:
    op.drop_table("memory_embeddings")
