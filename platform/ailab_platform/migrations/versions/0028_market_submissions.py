"""Skill marketplace submissions and review.

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_listings",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("slug", sa.String(40), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("license", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("current_version", sa.String(20)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_market_listings_user_id", "market_listings", ["user_id"])
    op.create_index("ix_market_listings_status_created", "market_listings", ["status", "created_at", "id"])
    op.create_index("ix_market_listings_owner_created", "market_listings", ["user_id", "created_at", "id"])
    op.create_index("ix_market_listings_slug", "market_listings", ["slug"], unique=True)

    op.create_table(
        "market_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("listing_id", sa.String(40), sa.ForeignKey("market_listings.id"), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("file_id", sa.String(40), sa.ForeignKey("files.id"), nullable=False),
        sa.Column("changelog", sa.String(2000), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("review_reason", sa.String(1000), nullable=False),
        sa.Column("reviewed_by", sa.String(40), sa.ForeignKey("users.id")),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("listing_id", "version", name="uq_market_versions_listing_version"),
    )
    op.create_index("ix_market_versions_listing_id", "market_versions", ["listing_id"])
    op.create_index("ix_market_versions_status_created", "market_versions", ["status", "created_at", "id"])

    op.create_table(
        "market_reviews",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("version_id", sa.String(40), sa.ForeignKey("market_versions.id"), nullable=False),
        sa.Column("reviewer_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_market_reviews_version_created", "market_reviews", ["version_id", "created_at"])


def downgrade():
    op.drop_index("ix_market_reviews_version_created", table_name="market_reviews")
    op.drop_table("market_reviews")
    op.drop_index("ix_market_versions_status_created", table_name="market_versions")
    op.drop_index("ix_market_versions_listing_id", table_name="market_versions")
    op.drop_table("market_versions")
    op.drop_index("ix_market_listings_slug", table_name="market_listings")
    op.drop_index("ix_market_listings_owner_created", table_name="market_listings")
    op.drop_index("ix_market_listings_status_created", table_name="market_listings")
    op.drop_index("ix_market_listings_user_id", table_name="market_listings")
    op.drop_table("market_listings")
