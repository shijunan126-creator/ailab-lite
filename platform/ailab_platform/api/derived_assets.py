"""API-only capacity authority. Reserve/commit before I/O; settle with publication afterwards.

The budget row serializes writers on both databases. Objects remain charged while
storage is unavailable, including after the business owner has been purged.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, or_, select, update

from ..config import settings
from ..models import Conversation, DerivedAsset, DerivedAssetRef, DerivedBudget, FileObject, Run, ViewerCapture
from ..storage import _safe_key

log = logging.getLogger("ailab.api.derived_assets")

PREFIXES = ("derived/", "viewer/", "previews/")


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def budget_lock(db):
    # An UPDATE acquires SQLite's writer lock as well as PostgreSQL's row lock.
    # Every ledger mutation takes this lock before inspecting asset state.
    db.execute(update(DerivedBudget).where(DerivedBudget.id == 1).values(id=1))
    row = db.get(DerivedBudget, 1, populate_existing=True)
    if row is None:
        raise HTTPException(503, "derived_budget_not_migrated")
    return row


def source(db, key, version=None, *, writing=True):
    try:
        _safe_key(key)
    except ValueError:
        raise HTTPException(422, "invalid_derived_key") from None
    parts = key.split("/")
    if len(key) > 600 or len(parts) < 3 or parts[0] + "/" not in PREFIXES:
        raise HTTPException(422, "invalid_derived_key")
    file = db.get(FileObject, parts[1], populate_existing=True) if parts[0] == "derived" else None
    run = db.get(Run, parts[1], populate_existing=True) if parts[0] != "derived" else None
    if file:
        if file.purged_at or (writing and (file.deleted_at or (file.meta or {}).get("scan", {}).get("verdict") != "clean")):
            raise HTTPException(409, "file_source_unavailable")
        if version is not None and file.sha256 != version:
            raise HTTPException(409, "document_version_changed")
        return file.id, None, file.sha256
    if run:
        conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
        if writing and run.conversation_id and (not conversation or conversation.deleted_at):
            raise HTTPException(409, "document_source_unavailable")
        return None, run.id, version or "legacy"
    raise HTTPException(409, "document_source_unavailable")


def reserve(db, key, maximum, version, *, kind="cache", reconstructible=True, now=None):
    """Caller commits this reservation before the first byte is written."""
    if type(maximum) is not int or not 0 <= maximum <= settings.derived_budget_bytes:
        raise HTTPException(413, "derived_capacity_exceeded")
    now = now or datetime.now(UTC)
    file_id, run_id, source_version = source(db, key, version)
    ledger = budget_lock(db)
    if not ledger.backfill_complete:
        raise HTTPException(409, "derived_accounting_initializing")
    existing = db.get(DerivedAsset, key, populate_existing=True)
    if existing and existing.state != "ready":
        raise HTTPException(409, "derived_write_in_progress")
    if existing and db.scalar(select(DerivedAssetRef.id).where(DerivedAssetRef.key == key).limit(1)):
        raise HTTPException(409, "derived_asset_in_use")
    won = db.execute(
        update(DerivedBudget)
        .where(
            DerivedBudget.id == 1,
            DerivedBudget.used_bytes + DerivedBudget.reserved_bytes + maximum <= settings.derived_budget_bytes,
        )
        .values(reserved_bytes=DerivedBudget.reserved_bytes + maximum)
    ).rowcount
    if not won:
        raise HTTPException(507, "derived_capacity_exceeded")
    token = uuid4().hex
    expiry = now + timedelta(days=settings.derived_cache_days) if reconstructible and kind not in {"native", "ocr"} else None
    if existing is None:
        existing = DerivedAsset(key=key, size_bytes=0)
        db.add(existing)
    existing.file_id, existing.run_id = file_id, run_id
    existing.source_version = source_version
    existing.kind, existing.state, existing.token = kind, "writing", token
    existing.reserved_bytes = maximum
    existing.reconstructible, existing.created_at, existing.expires_at = reconstructible, now, expiry
    existing.lease_until = now + timedelta(seconds=settings.derived_write_lease_seconds)
    existing.retired_at = None
    db.flush()
    return {"key": key, "token": token, "max_bytes": maximum, "lease_until": existing.lease_until.isoformat()}


def finish(db, key, token, size, digest, *, now=None, published=True):
    """Settle actual occupancy in the same transaction as derived metadata publication."""
    now = now or datetime.now(UTC)
    ledger = budget_lock(db)
    row = db.get(DerivedAsset, key, populate_existing=True)
    if not row or row.token != token:
        raise HTTPException(409, "derived_generation_stale")
    source(db, key, row.source_version)
    if row.state in {"ready", "staged"} and row.size_bytes == size and row.digest == digest:
        return row
    if row.state != "writing" or not row.lease_until or aware(row.lease_until) <= now:
        raise HTTPException(409, "derived_generation_stale")
    if type(size) is not int or not 0 <= size <= row.reserved_bytes:
        raise HTTPException(413, "derived_reservation_exceeded")
    ledger.used_bytes += size - row.size_bytes
    ledger.reserved_bytes -= row.reserved_bytes
    row.size_bytes, row.digest = size, digest
    row.reserved_bytes = 0
    row.state = "ready" if published else "staged"
    row.lease_until = None if published else now + timedelta(seconds=settings.derived_write_lease_seconds)
    db.flush()
    return row


def publish(db, key, token, *, now=None):
    """Bind staged bytes in the business metadata transaction, before its ACK."""
    now = now or datetime.now(UTC)
    budget_lock(db)
    row = db.get(DerivedAsset, key, populate_existing=True)
    if not row or row.token != token or row.state not in {"staged", "ready"}:
        raise HTTPException(409, "derived_generation_stale")
    source(db, key, row.source_version)
    if row.state == "staged" and (not row.lease_until or aware(row.lease_until) <= now):
        raise HTTPException(409, "derived_generation_stale")
    row.state, row.lease_until = "ready", None
    db.flush()
    return row


def abandon(db, key, token):
    """Keep liability until a successful physical delete, even for failed uploads."""
    budget_lock(db)
    row = db.get(DerivedAsset, key, populate_existing=True)
    if row and row.token == token and row.state in {"writing", "staged"}:
        row.state = "deleting"
        row.lease_until = None
        row.expires_at = datetime.now(UTC)
        db.flush()


def pin(db, key, identity, *, kind, run_id=None, expires_at=None):
    budget_lock(db)
    asset = db.get(DerivedAsset, key, populate_existing=True)
    if not asset or asset.state != "ready":
        raise HTTPException(409, "derived_asset_unavailable")
    current = db.get(DerivedAssetRef, identity)
    if current and (current.key != key or current.run_id != run_id or current.kind != kind):
        raise HTTPException(409, "derived_reference_conflict")
    if not current:
        db.add(DerivedAssetRef(id=identity, key=key, kind=kind, run_id=run_id, expires_at=expires_at))
    else:
        current.expires_at = expires_at
    db.flush()


def unpin(db, identity):
    budget_lock(db)
    ref = db.get(DerivedAssetRef, identity)
    if not ref:
        return
    key = ref.key
    db.delete(ref)
    db.flush()
    asset = db.get(DerivedAsset, key, populate_existing=True)
    if asset and not asset.reconstructible and not protected(db, asset, datetime.now(UTC)):
        asset.state, asset.lease_until, asset.expires_at = "deleting", None, datetime.now(UTC)
        db.flush()


def retire(db, key, *, now=None):
    budget_lock(db)
    row = db.get(DerivedAsset, key, populate_existing=True)
    if row and row.state == "ready":
        now = now or datetime.now(UTC)
        row.retired_at = now
        due = now + timedelta(seconds=settings.derived_generation_grace_seconds)
        row.expires_at = min(aware(row.expires_at), due) if row.expires_at else due
        db.flush()


def protected(db, row, now):
    return (
        db.scalar(
            select(DerivedAssetRef.id)
            .where(
                DerivedAssetRef.key == row.key,
                or_(DerivedAssetRef.expires_at.is_(None), DerivedAssetRef.expires_at > now),
                or_(DerivedAssetRef.kind == "transfer", not row.reconstructible),
            )
            .limit(1)
        )
        is not None
    )


def forget_deleted(db, key):
    """Call only after storage.delete succeeded; never release space on intent alone."""
    ledger = budget_lock(db)
    row = db.get(DerivedAsset, key, populate_existing=True)
    if row:
        ledger.used_bytes -= row.size_bytes
        ledger.reserved_bytes -= row.reserved_bytes
        db.delete(row)
        db.flush()


def backfill_batch(db, storage, *, now=None):
    """One persisted cursor page per call; idempotent across crashes and replicas."""
    now = now or datetime.now(UTC)
    snapshot = db.get(DerivedBudget, 1, populate_existing=True)
    if not snapshot or snapshot.backfill_complete:
        return 0
    position = (snapshot.backfill_prefix, snapshot.backfill_cursor)
    db.rollback()
    prefix = PREFIXES[position[0]]
    page = storage.list_prefix(prefix, cursor=position[1], limit=settings.gc_batch_size)
    ledger = budget_lock(db)
    if ledger.backfill_complete or (ledger.backfill_prefix, ledger.backfill_cursor) != position:
        return 0
    keys = [item.key for item in page if len(item.key.split("/")) >= 3]
    known = set(db.scalars(select(DerivedAsset.key).where(DerivedAsset.key.in_(keys))))
    owner_ids = {key.split("/")[1] for key in keys}
    files = {f.id: f for f in db.scalars(select(FileObject).where(FileObject.id.in_(owner_ids)))} if prefix == "derived/" else {}
    runs = {r.id: r for r in db.scalars(select(Run).where(Run.id.in_(owner_ids)))} if prefix != "derived/" else {}
    captures = {c.storage_key: c for c in db.scalars(select(ViewerCapture).where(ViewerCapture.storage_key.in_(keys)))}
    rows, refs = [], []
    for item in page:
        if item.key not in keys or item.key in known:
            continue
        owner = item.key.split("/")[1]
        file, run, capture = files.get(owner), runs.get(owner), captures.get(item.key)
        native = item.key.endswith((".txt", "manifest.json"))
        orphan = (not file or file.purged_at) if prefix == "derived/" else not run
        row = DerivedAsset(
            key=item.key,
            file_id=owner if prefix == "derived/" else None,
            run_id=owner if prefix != "derived/" else None,
            source_version=file.sha256 if file else "legacy",
            kind="capture" if capture else "native" if native else "cache",
            state="ready",
            token=uuid4().hex,
            size_bytes=item.size,
            reserved_bytes=0,
            reconstructible=capture is None,
            created_at=item.modified_at,
            expires_at=(
                now + timedelta(seconds=settings.derived_orphan_grace_seconds)
                if orphan
                else None
                if native or capture
                else aware(item.modified_at) + timedelta(days=settings.derived_cache_days)
            ),
        )
        rows.append(row)
        if capture:
            refs.append(DerivedAssetRef(id="capture:" + capture.id, key=item.key, kind="message", run_id=capture.run_id))
    db.add_all(rows)
    db.flush()
    db.add_all(refs)
    ledger.used_bytes += sum(row.size_bytes for row in rows)
    count = len(rows)
    ledger.backfill_cursor = page.cursor
    if page.cursor is None:
        ledger.backfill_prefix += 1
        ledger.backfill_complete = ledger.backfill_prefix == len(PREFIXES)
    db.flush()
    return count


def collect_batch(db, storage, *, now=None, transient_only=False):
    """Failures leave durable rows and charged bytes for the next bounded pass."""
    now = now or datetime.now(UTC)
    expired = list(db.scalars(select(DerivedAssetRef.id).where(DerivedAssetRef.expires_at <= now).limit(settings.gc_batch_size)))
    if expired:
        db.execute(delete(DerivedAssetRef).where(DerivedAssetRef.id.in_(expired)))
        db.commit()
    orphan_cutoff = now - timedelta(seconds=settings.derived_orphan_grace_seconds)
    unreferenced_capture = (
        ~select(DerivedAssetRef.id)
        .where(
            DerivedAssetRef.key == DerivedAsset.key,
            or_(DerivedAssetRef.expires_at.is_(None), DerivedAssetRef.expires_at > now),
        )
        .exists()
    )
    queries = [
        select(DerivedAsset.key)
        .where(
            DerivedAsset.state == "ready",
            DerivedAsset.reconstructible.is_(False),
            DerivedAsset.created_at <= orphan_cutoff,
            unreferenced_capture,
        )
        .order_by(DerivedAsset.created_at, DerivedAsset.key),
        select(DerivedAsset.key)
        .where(DerivedAsset.state == "deleting", or_(DerivedAsset.lease_until.is_(None), DerivedAsset.lease_until <= now))
        .order_by(DerivedAsset.lease_until, DerivedAsset.key),
        select(DerivedAsset.key)
        .where(DerivedAsset.state.in_(["writing", "staged"]), DerivedAsset.lease_until <= orphan_cutoff)
        .order_by(DerivedAsset.lease_until, DerivedAsset.key),
        select(DerivedAsset.key)
        .where(DerivedAsset.state == "ready", DerivedAsset.expires_at <= now)
        .order_by(DerivedAsset.expires_at, DerivedAsset.key),
    ]
    if transient_only:
        # Until all legacy business metadata has expiry invalidation, do not
        # collect published caches. Unpublished failed/crashed writes are safe.
        queries = queries[1:3]
    removed, examined = 0, 0
    for query in queries:
        keys = list(db.scalars(query.limit(settings.gc_batch_size - examined)))
        for key in keys:
            examined += 1
            budget_lock(db)
            row = db.get(DerivedAsset, key, populate_existing=True)
            if (
                not row
                or row.state not in {"ready", "deleting", "writing", "staged"}
                or (row.state in {"writing", "staged"} and (not row.lease_until or aware(row.lease_until) > orphan_cutoff))
                or (row.state == "ready" and row.reconstructible and (not row.expires_at or aware(row.expires_at) > now))
                or (row.state == "ready" and not row.reconstructible and row.created_at and aware(row.created_at) > orphan_cutoff)
                or (row.state == "deleting" and row.lease_until and aware(row.lease_until) > now)
                or protected(db, row, now)
            ):
                db.rollback()
                continue
            token = uuid4().hex
            row.state, row.token = "deleting", token
            row.lease_until = now + timedelta(seconds=settings.derived_write_lease_seconds)
            db.commit()
            try:
                storage.delete(key)
            except Exception:
                db.rollback()
                log.warning("derived cleanup failed; capacity remains charged", exc_info=True)
                continue
            budget_lock(db)
            current = db.get(DerivedAsset, key, populate_existing=True)
            if current and current.state == "deleting" and current.token == token:
                forget_deleted(db, key)
                db.commit()
                removed += 1
            else:
                db.rollback()
        if examined >= settings.gc_batch_size:
            break
    return removed
