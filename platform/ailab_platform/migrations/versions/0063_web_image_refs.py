"""Address-only web images with run-scoped references; no stored picture bytes."""

import sqlalchemy as sa
from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "web_images",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("url_hash", sa.String(64), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("thumbnail_url", sa.Text(), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("uq_web_images_url_hash", "web_images", ["url_hash"], unique=True)
    op.create_index("ix_web_images_expiry", "web_images", ["expires_at", "id"])
    op.create_table(
        "web_image_refs",
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("image_id", sa.String(24), sa.ForeignKey("web_images.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_web_image_refs_image", "web_image_refs", ["image_id", "run_id"])
    op.create_index("ix_web_image_refs_expiry", "web_image_refs", ["expires_at", "run_id", "image_id"])


def downgrade():
    op.drop_table("web_image_refs")
    op.drop_table("web_images")
