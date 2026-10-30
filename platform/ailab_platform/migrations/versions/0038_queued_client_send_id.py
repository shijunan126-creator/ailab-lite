"""Durable user-scoped queue submission identity."""

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade():
    # Legacy options may contain duplicate identities and lack a request digest; leave them unclaimed.
    op.add_column("queued_messages", sa.Column("client_send_id", sa.String(64), nullable=True))
    op.create_index("uq_queued_messages_user_client_send", "queued_messages", ["user_id", "client_send_id"], unique=True)


def downgrade():
    op.drop_index("uq_queued_messages_user_client_send", table_name="queued_messages")
    op.drop_column("queued_messages", "client_send_id")
