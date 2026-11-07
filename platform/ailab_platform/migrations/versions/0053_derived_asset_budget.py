"""Account every persistent derivative and retain deletion liability after owner purge."""

import sqlalchemy as sa
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_viewer_captures_key", "viewer_captures", ["storage_key"])
    op.create_table(
        "derived_budget",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("backfill_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("backfill_prefix", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("backfill_cursor", sa.Text(), nullable=True),
        sa.CheckConstraint("used_bytes >= 0 AND reserved_bytes >= 0", name="ck_derived_budget_nonnegative"),
    )
    op.execute(sa.text("INSERT INTO derived_budget (id) VALUES (1)"))
    op.create_table(
        "derived_assets",
        sa.Column("key", sa.String(600), primary_key=True),
        sa.Column("file_id", sa.String(40), nullable=True),
        sa.Column("run_id", sa.String(40), nullable=True),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("token", sa.String(40), nullable=False),
        sa.Column("job_id", sa.String(40), nullable=True),
        sa.Column("attempt_token", sa.String(40), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("digest", sa.String(100), nullable=True),
        sa.Column("reconstructible", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("size_bytes >= 0 AND reserved_bytes >= 0", name="ck_derived_assets_nonnegative"),
    )
    op.create_index("ix_derived_assets_state_expiry", "derived_assets", ["state", "expires_at", "key"])
    op.create_index("ix_derived_assets_state_lease", "derived_assets", ["state", "lease_until", "key"])
    op.create_index("ix_derived_assets_file", "derived_assets", ["file_id", "source_version"])
    op.create_index("ix_derived_assets_run", "derived_assets", ["run_id"])
    op.create_index("ix_derived_assets_captures", "derived_assets", ["state", "reconstructible", "created_at", "key"])
    op.create_table(
        "derived_asset_refs",
        sa.Column("id", sa.String(160), primary_key=True),
        sa.Column("key", sa.String(600), sa.ForeignKey("derived_assets.key", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_derived_asset_refs_key", "derived_asset_refs", ["key", "kind", "expires_at"])
    op.create_index("ix_derived_asset_refs_run", "derived_asset_refs", ["run_id"])
    op.create_index("ix_derived_asset_refs_expiry", "derived_asset_refs", ["expires_at"])


def downgrade():
    op.drop_table("derived_asset_refs")
    op.drop_table("derived_assets")
    op.drop_table("derived_budget")
    op.drop_index("ix_viewer_captures_key", table_name="viewer_captures")
