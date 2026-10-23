"""Call reservations and immutable fixed-point usage ledger."""

from collections.abc import Sequence
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

import sqlalchemy as sa
from alembic import op

from ailab_platform.config import settings

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = [
        sa.Column("call_id", sa.String(80), nullable=True),
        sa.Column("user_id", sa.String(40), nullable=True),
        sa.Column("dept_id", sa.String(40), nullable=True),
        sa.Column("attempt_id", sa.String(80), nullable=False, server_default=""),
        sa.Column("mode", sa.String(20), nullable=False, server_default="work"),
        sa.Column("alias", sa.String(120), nullable=False, server_default=""),
        sa.Column("actual_backend", sa.String(120), nullable=False, server_default=""),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_token_weight", sa.Numeric(20, 9), nullable=False, server_default="0.1"),
        sa.Column("weighted_amount", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("policy_version", sa.String(40), nullable=False, server_default="legacy"),
        sa.Column("status", sa.String(20), nullable=False, server_default="settled"),
    ]
    for column in columns:
        op.add_column("model_usage", column)
    conn = op.get_bind()
    usage = sa.table(
        "model_usage",
        sa.column("id"),
        sa.column("run_id"),
        sa.column("model"),
        sa.column("prompt_tokens"),
        sa.column("cached_tokens"),
        sa.column("completion_tokens"),
        sa.column("weight"),
        sa.column("call_id"),
        sa.column("user_id"),
        sa.column("mode"),
        sa.column("alias"),
        sa.column("cached_token_weight", sa.Numeric(20, 9)),
        sa.column("weighted_amount"),
    )
    runs = sa.table("runs", sa.column("id"), sa.column("user_id"), sa.column("options", sa.JSON))
    cached_weight = Decimal(str(settings.cached_token_weight)).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
    after = 0
    while True:
        rows = (
            conn.execute(
                sa.select(usage, runs.c.user_id.label("owner"), runs.c.options)
                .join(runs, usage.c.run_id == runs.c.id)
                .where(usage.c.id > after)
                .order_by(usage.c.id)
                .limit(500)
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        for row in rows:
            cached = min(row["cached_tokens"], row["prompt_tokens"])
            conn.execute(
                usage.update()
                .where(usage.c.id == row["id"])
                .values(
                    call_id=f"legacy-{row['id']}",
                    user_id=row["owner"],
                    mode=(row["options"] or {}).get("mode", "work"),
                    alias=row["model"],
                    cached_token_weight=cached_weight,
                    weighted_amount=int(
                        (
                            (Decimal(row["prompt_tokens"] - cached + row["completion_tokens"]) + cached * cached_weight)
                            * Decimal(str(row["weight"])).quantize(Decimal("0.000000001"), rounding=ROUND_HALF_UP)
                            * 1000
                        ).to_integral_value(rounding=ROUND_CEILING)
                    ),
                )
            )
        after = rows[-1]["id"]
    op.create_index("ix_model_usage_call_id", "model_usage", ["call_id"], unique=True)
    op.create_index("ix_model_usage_user_created", "model_usage", ["user_id", "created_at"])
    op.create_index("ix_model_usage_status_id", "model_usage", ["status", "id"])
    op.create_table(
        "usage_reservations",
        sa.Column("call_id", sa.String(80), primary_key=True),
        sa.Column("run_id", sa.String(40), nullable=False),  # a value, not a reference: see 0012
        sa.Column("user_id", sa.String(40), nullable=False),
        sa.Column("attempt_id", sa.String(80), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("alias", sa.String(120), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_input_tokens", sa.Integer(), nullable=False),
        sa.Column("weight", sa.Numeric(20, 9), nullable=False),
        sa.Column("cached_token_weight", sa.Numeric(20, 9), nullable=False),
        sa.Column("policy_version", sa.String(40), nullable=False),
        sa.Column("reserved_amount", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_reservations_user_status", "usage_reservations", ["user_id", "status"])
    op.create_index("ix_usage_reservations_status_expires", "usage_reservations", ["status", "expires_at", "call_id"])
    op.create_index("ix_usage_reservations_run", "usage_reservations", ["run_id"])


def downgrade() -> None:
    op.drop_table("usage_reservations")
    for index in ("ix_model_usage_call_id", "ix_model_usage_user_created", "ix_model_usage_status_id"):
        op.drop_index(index, table_name="model_usage")
    for name in (
        "status",
        "policy_version",
        "weighted_amount",
        "cached_token_weight",
        "reasoning_tokens",
        "actual_backend",
        "alias",
        "mode",
        "attempt_id",
        "dept_id",
        "user_id",
        "call_id",
    ):
        op.drop_column("model_usage", name)
