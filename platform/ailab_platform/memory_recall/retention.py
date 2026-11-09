"""Bounded historical-version collection; current revision undo references remain authoritative."""

from datetime import timedelta

from sqlalchemy import delete, select

from ..config import settings
from ..locks import user_critical_section
from ..models import MemoryDreamRun, MemoryItem, User


def purge_closed(db, now):
    candidates = list(
        db.execute(
            select(MemoryItem.id, MemoryItem.user_id)
            .where(MemoryItem.status == "closed", MemoryItem.valid_to < now - timedelta(days=90))
            .order_by(MemoryItem.valid_to, MemoryItem.id)
            .limit(settings.gc_batch_size)
        )
    )
    removed = 0
    for uid in {row.user_id for row in candidates}:
        with user_critical_section(db, uid):
            user = db.get(User, uid, populate_existing=True)
            revision = ((user.settings or {}).get("memory") or {}).get("revision", 0) if user else None
            # Undo is revision fenced, so only the matching applied delta can still be restored.
            protected = set()
            for delta in db.scalars(
                select(MemoryDreamRun.change_set).where(
                    MemoryDreamRun.user_id == uid,
                    MemoryDreamRun.status == "applied",
                    MemoryDreamRun.change_set["revision"].as_integer() == revision,
                )
            ):
                protected.update(item["id"] for item in (delta or {}).get("items", []))
            ids = [row.id for row in candidates if row.user_id == uid and row.id not in protected]
            if ids:
                removed += db.execute(
                    delete(MemoryItem).where(
                        MemoryItem.id.in_(ids), MemoryItem.status == "closed", MemoryItem.valid_to < now - timedelta(days=90)
                    )
                ).rowcount
            db.commit()
    return removed
