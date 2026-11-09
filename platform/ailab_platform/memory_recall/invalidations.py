"""Resolve only already-known handles through indexed version links, never scan historical memory."""

from sqlalchemy import select

from ..models import MemoryItem
from .policy import visible
from .store import legacy


def for_handles(db, user_id, handles, document, project_id, mode):
    if not document.get("enabled", True) or not document.get("read_enabled", True):
        return {}
    active = {e["id"]: e for e in document["entries"]}
    rows = {}
    pending = set(handles) - active.keys()
    while pending:
        loaded = list(db.scalars(select(MemoryItem).where(MemoryItem.user_id == user_id, MemoryItem.id.in_(pending))))
        rows.update({row.id: row for row in loaded})
        pending = {
            row.superseded_by for row in loaded if row.superseded_by and row.superseded_by not in rows and row.superseded_by not in active
        }
    out = {}
    for handle in handles:
        current, seen, correction, condensed = handle, set(), False, False
        while current in rows and current not in seen:
            seen.add(current)
            row = rows[current]
            change = (row.metadata_ or {}).get("change_type")
            correction |= change == "correction"
            condensed |= change == "condense"
            if not row.superseded_by:
                break
            current = row.superseded_by
        target = active.get(current)
        if target is None and current in rows and rows[current].status != "closed":
            target = legacy(rows[current])
        if target and not visible(target, project_id, mode=mode):
            target = None
        out[handle] = {
            "current": target,
            "change_type": "correction" if correction else "condense" if condensed else (target or {}).get("change_type"),
        }
    return out
