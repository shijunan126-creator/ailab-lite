"""Small owner-bound screenshot derivatives, with explicit expiry and source identity."""

import base64
import binascii
import hashlib
import struct
import zlib
from datetime import UTC, datetime

from fastapi import HTTPException, Response
from sqlalchemy import select

from ..models import Conversation, DerivedAsset, DerivedAssetRef, Run, RunStatus, User, ViewerCapture
from ..storage import get_storage

MAX_IMAGE = 2 * 1024**2


def validate_png(value):
    if (
        not isinstance(value, dict)
        or set(value) - {"image", "mime_type", "width", "height", "at", "version"}
        or value.get("mime_type") != "image/png"
        or not isinstance(value.get("image"), str)
    ):
        raise HTTPException(422, "PNG capture required")
    if len(value["image"]) > 4 * ((MAX_IMAGE + 2) // 3):
        raise HTTPException(413, "capture exceeds limit")
    try:
        data = base64.b64decode(value["image"], validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "invalid capture encoding") from None
    if len(data) > MAX_IMAGE:
        raise HTTPException(413, "capture exceeds limit")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(422, "invalid PNG")
    offset, dimensions, image_data, ended = 8, None, False, False
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4], "big")
        chunk_type = data[offset + 4 : offset + 8]
        end = offset + 12 + length
        if end > len(data) or zlib.crc32(data[offset + 4 : end - 4]) != int.from_bytes(data[end - 4 : end], "big"):
            raise HTTPException(422, "invalid PNG chunk")
        if offset == 8:
            if chunk_type != b"IHDR" or length != 13:
                raise HTTPException(422, "invalid PNG header")
            dimensions = struct.unpack(">II", data[offset + 8 : offset + 16])
        if chunk_type == b"IDAT":
            image_data = True
        if chunk_type == b"IEND":
            ended = length == 0 and end == len(data)
            break
        offset = end
    if (
        not ended
        or not image_data
        or not dimensions
        or not all(type(value.get(k)) is int and 1 <= value[k] <= 8192 for k in ("width", "height"))
    ):
        raise HTTPException(422, "invalid PNG dimensions")
    if dimensions != (value["width"], value["height"]) or dimensions[0] * dimensions[1] > 16_000_000:
        raise HTTPException(422, "invalid PNG dimensions")
    return data


def save(db, run, identity, target, data, mime="image/png", *, validate=None, bind=None):
    """Publish one canonical object and its receipt in the same transaction.

    Browser pixels cannot be reconstructed deterministically; they are retained with
    the originating message.
    """
    from . import derived_assets as assets
    from .owned_assets import publish
    from .routes_document_view import authorize_state

    run_id, owner = run.id, run.user_id
    capture_id = hashlib.sha256((run_id + ":" + identity).encode()).hexdigest()
    file_id = target.get("file_id")
    prefix = f"derived/{file_id}" if file_id else f"viewer/{run_id}"
    key = f"{prefix}/captures/{capture_id}"
    digest = hashlib.sha256(data).hexdigest()
    receipt = {"status": "ready", "capture_id": capture_id, "version": target["version"], "source": target["source"]}

    def check():
        current = db.get(Run, run_id, populate_existing=True)
        conversation = (
            db.get(Conversation, current.conversation_id, populate_existing=True) if current and current.conversation_id else None
        )
        user = db.get(User, owner, populate_existing=True)
        if (
            not current
            or current.user_id != owner
            or (
                current.status != RunStatus.RUNNING
                and not (
                    current.status == RunStatus.INTERRUPTED
                    and ((current.runtime_meta or {}).get("file_dependency") or {}).get("action") == "document-view"
                )
            )
            or not user
            or user.disabled_at
            or (current.conversation_id and (not conversation or conversation.deleted_at))
        ):
            raise HTTPException(409, "capture_source_unavailable")
        authorize_state(db, user, target)
        previous = db.get(ViewerCapture, capture_id, populate_existing=True)
        if previous and (previous.digest != digest or previous.target != target or previous.content_type != mime):
            raise HTTPException(409, "capture_identity_changed")
        if validate:
            validate()

    def attach(storage_key):
        if db.get(ViewerCapture, capture_id) is None:
            db.add(
                ViewerCapture(
                    id=capture_id,
                    run_id=run_id,
                    file_id=file_id,
                    target=target,
                    storage_key=storage_key,
                    digest=digest,
                    content_type=mime,
                    expires_at=None,
                )
            )
        assets.pin(db, storage_key, "capture:" + capture_id, kind="message", run_id=run_id, expires_at=None)
        if bind:
            bind(receipt)
        return receipt

    return publish(
        db,
        owner,
        key,
        data,
        target["version"],
        kind="capture",
        reconstructible=False,
        validate=check,
        bind=attach,
        content_type=mime,
    )


def image(db, run_id, capture_id):
    capture = db.get(ViewerCapture, capture_id)
    run = db.get(Run, run_id)
    if not run or not capture:
        raise HTTPException(404, "capture unavailable")
    if capture.run_id != run.id:
        from ..conversation_tree import ancestor_query

        # Checkpoints keep the exact image across turns, but only the selected
        # conversation branch may inherit its originating run's capture.
        origin = db.get(Run, capture.run_id)
        ancestors = ancestor_query(run.conversation_id, run.parent_run_id)
        if (
            not origin
            or origin.user_id != run.user_id
            or not run.conversation_id
            or not db.scalar(select(ancestors.c.id).where(ancestors.c.id == capture.run_id).limit(1))
        ):
            raise HTTPException(404, "capture unavailable")
    expiry = capture.expires_at
    expiry = expiry.replace(tzinfo=UTC) if expiry is not None and expiry.tzinfo is None else expiry
    if expiry is not None and expiry <= datetime.now(UTC):
        raise HTTPException(410, "capture expired")
    from .routes_document_view import authorize_state

    authorize_state(db, db.get(User, run.user_id), capture.target)
    data = get_storage().get_bytes(capture.storage_key)
    if len(data) > MAX_IMAGE or hashlib.sha256(data).hexdigest() != capture.digest:
        raise HTTPException(410, "capture unavailable")
    return Response(data, media_type=capture.content_type, headers={"Cache-Control": "private, no-store"})


def purge(db, storage, now):
    """Legacy TTL has a durable delete intent; capacity is released after I/O."""
    from ..config import settings
    from ..library import schedule_object_deletion
    from . import derived_assets as assets

    rows = list(
        db.scalars(
            select(ViewerCapture).where(ViewerCapture.expires_at <= now).order_by(ViewerCapture.expires_at).limit(settings.gc_batch_size)
        )
    )
    for row in rows:
        assets.unpin(db, "capture:" + row.id)
        schedule_object_deletion(db, row.storage_key, "capture")
        db.delete(row)
    db.commit()
    return len(rows)


def release_runs(db, run_ids):
    """Release screenshot ownership with source invalidation/conversation GC."""
    from sqlalchemy import delete, update

    from .derived_assets import budget_lock

    budget_lock(db)
    keys = select(ViewerCapture.storage_key).where(ViewerCapture.run_id.in_(run_ids))
    db.execute(
        delete(DerivedAssetRef).where(DerivedAssetRef.key.in_(keys), DerivedAssetRef.kind == "message", DerivedAssetRef.run_id.in_(run_ids))
    )
    surviving = select(DerivedAssetRef.id).where(DerivedAssetRef.key == DerivedAsset.key).exists()
    db.execute(
        update(DerivedAsset)
        .where(DerivedAsset.key.in_(keys), ~surviving)
        .values(state="deleting", lease_until=None, expires_at=datetime.now(UTC))
    )
    db.flush()
