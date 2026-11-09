"""Relational source of truth with a temporary legacy DTO adapter."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import object_session

from ..models import MemoryItem, utcnow
from .tombstones import fact_hash


def stamp(value, fallback=None):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value is None:
        return fallback
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def legacy(row):
    return {
        **(row.metadata_ or {}),
        "id": row.id,
        "project_id": row.project_id,
        "kind": {"preference": "user", "fact": "memory"}.get(row.kind, row.kind),
        "origin": row.origin,
        "content": row.content,
        "evidence_run_ids": row.source_run_ids,
        "source_quote": row.source_quote,
        "observed_at": stamp(row.observed_at).isoformat(),
        "valid_from": stamp(row.valid_from).isoformat(),
        "invalid_at": row.valid_to.isoformat() if row.valid_to else None,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "superseded_by": row.superseded_by,
        "status": row.status,
        "suppressed": row.status == "suppressed",
        "confidence": row.confidence,
        "updated_at": stamp(row.updated_at).isoformat(),
        "reinforced_at": stamp(row.reinforced_at).isoformat(),
    }


def entries_for(user):
    db = object_session(user)
    if db is None:
        return []
    return [
        legacy(row)
        for row in db.scalars(
            select(MemoryItem)
            .where(MemoryItem.user_id == user.id, MemoryItem.status.in_(("active", "review", "suppressed")))
            .order_by(MemoryItem.created_at, MemoryItem.id)
        )
    ]


def replace_entries(db, user, entries):
    """Caller owns the user lock and revision CAS; changes join that transaction."""
    rows = {
        r.id: r
        for r in db.scalars(
            select(MemoryItem).where(MemoryItem.user_id == user.id, MemoryItem.status.in_(("active", "review", "suppressed")))
        )
    }
    ids = {e["id"] for e in entries} - rows.keys()
    if ids:
        rows.update({r.id: r for r in db.scalars(select(MemoryItem).where(MemoryItem.user_id == user.id, MemoryItem.id.in_(ids)))})
    keep = {e["id"] for e in entries}
    for key, row in rows.items():
        if key not in keep:
            db.delete(row)
    # Close superseded rows before inserts so the partial unique index sees the final state.
    for e in entries:
        if e["id"] in rows and (e.get("invalid_at") or e.get("suppressed")):
            rows[e["id"]].status = "suppressed" if e.get("suppressed") else "closed"
    db.flush()
    now = utcnow()
    for e in entries:
        row = rows.get(e["id"])
        fresh = row is None
        if fresh:
            row = MemoryItem(id=e["id"], user_id=user.id)
            db.add(row)
        row.project_id = e.get("project_id")
        row.kind = {"user": "preference", "memory": "fact"}.get(e["kind"], e["kind"])
        row.origin = e.get("origin") or ("explicit" if e.get("source") == "nightly" else "chat" if e.get("source") == "agent" else "edited")
        row.content = e["content"]
        row.source_run_ids = e.get("evidence_run_ids") or []
        row.source_quote = e.get("source_quote") or ""
        row.observed_at = stamp(e.get("observed_at"), row.observed_at if not fresh else stamp(e.get("valid_from"), now))
        row.valid_from = stamp(e.get("valid_from"), now)
        row.valid_to = stamp(e.get("invalid_at"))
        row.expires_at = stamp(e.get("expires_at"))
        row.superseded_by = e.get("superseded_by")
        row.status = "suppressed" if e.get("suppressed") else "closed" if row.valid_to else e.get("status", "active")
        row.confidence = e.get("confidence", 1.0)
        row.reinforced_at = stamp(e.get("reinforced_at"), row.reinforced_at if not fresh else now)
        row.content_hash = fact_hash(row.content)
        row.updated_at = stamp(e.get("updated_at"), now)
        row.metadata_ = {k: e[k] for k in ("source", "change_type", "evidence_episode_ids", "evidence_deleted", "last_used_at") if k in e}
    db.flush()
