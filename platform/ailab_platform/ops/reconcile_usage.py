"""Read-only ledger audit. Run from the API environment; never repairs or reprices history."""

from __future__ import annotations

import argparse
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..contracts.usage import UsageStatus
from ..db import SessionLocal
from ..models import ModelUsage, Run


def reconcile(db: Session, batch_size: int = 200, after_usage_id: int = 0, after_run_id: str = "") -> dict:
    if not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be in 1..1000")
    candidates = []
    for status in (UsageStatus.UNRECONCILED, UsageStatus.ESTIMATED):
        candidates.extend(
            db.scalars(
                select(ModelUsage)
                .where(ModelUsage.status == status, ModelUsage.id > after_usage_id)
                .order_by(ModelUsage.id)
                .limit(batch_size)
            )
        )
    rows = sorted(candidates, key=lambda row: row.id)[:batch_size]
    batch = (
        select(Run.id, Run.result["usage_totals"].label("recorded"))
        .where(Run.id > after_run_id)
        .order_by(Run.id)
        .limit(batch_size)
        .subquery()
    )
    columns = ("prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens", "weighted_amount")
    totals = (
        select(
            ModelUsage.run_id,
            *(func.sum(getattr(ModelUsage, name)).label(name) for name in columns),
            func.count(ModelUsage.id).label("calls"),
        )
        .join(batch, batch.c.id == ModelUsage.run_id)
        .group_by(ModelUsage.run_id)
        .subquery()
    )
    # A single snapshot prevents a healthy concurrent settlement from appearing as drift.
    runs = db.execute(
        select(batch, *(totals.c[name] for name in (*columns, "calls")))
        .outerjoin(totals, totals.c.run_id == batch.c.id)
        .order_by(batch.c.id)
    ).all()
    mismatches = []
    for run in runs:
        actual = dict(zip((*columns, "calls"), (int(v or 0) for v in run[2:]), strict=True))
        if (run.recorded is not None or actual["calls"]) and run.recorded != actual:
            mismatches.append({"run_id": run.id, "recorded": run.recorded, "ledger": actual})
    return {
        "usage": [
            {"id": row.id, "call_id": row.call_id, "run_id": row.run_id, "status": row.status, "weighted_amount": row.weighted_amount}
            for row in rows
        ],
        "run_mismatches": mismatches,
        "next_usage_id": rows[-1].id if len(rows) == batch_size else None,
        "next_run_id": runs[-1].id if len(runs) == batch_size else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--after-usage-id", type=int, default=0)
    parser.add_argument("--after-run-id", default="")
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 1000 or args.after_usage_id < 0:
        parser.error("batch-size must be 1..1000 and after-usage-id nonnegative")
    with SessionLocal() as db:
        print(json.dumps(reconcile(db, args.batch_size, args.after_usage_id, args.after_run_id), ensure_ascii=False))


if __name__ == "__main__":
    main()
