"""Freeze department attribution when reserving a model call, before inference."""

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("usage_reservations", sa.Column("dept_id", sa.String(40), nullable=True))
    # Existing settled calls already have a historical snapshot. Open legacy calls
    # have no evidence of their original department, so leave it unknown.
    op.execute(
        sa.text(
            "UPDATE usage_reservations SET dept_id = "
            "(SELECT model_usage.dept_id FROM model_usage WHERE model_usage.call_id = usage_reservations.call_id) "
            "WHERE EXISTS (SELECT 1 FROM model_usage WHERE model_usage.call_id = usage_reservations.call_id)"
        )
    )


def downgrade():
    op.drop_column("usage_reservations", "dept_id")
