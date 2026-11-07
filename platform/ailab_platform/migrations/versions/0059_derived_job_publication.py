"""Bound staged-asset publication by durable writer identity."""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_derived_assets_job_attempt", "derived_assets", ["job_id", "attempt_token", "state"])


def downgrade():
    op.drop_index("ix_derived_assets_job_attempt", table_name="derived_assets")
