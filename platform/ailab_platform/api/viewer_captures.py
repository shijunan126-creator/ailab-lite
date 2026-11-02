"""Small owner-bound screenshot derivatives, with explicit expiry and source identity."""

import base64
import binascii
import hashlib
import re
import struct
import zlib
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Response
from sqlalchemy import select

from ..models import Run, User, ViewerCapture
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


def save(db, run, identity, target, data, mime="image/png"):
    capture_id = hashlib.sha256((run.id + ":" + identity).encode()).hexdigest()
    existing = db.get(ViewerCapture, capture_id)
    if existing:
        return {"status": "ready", "capture_id": capture_id, "version": target["version"], "source": target["source"]}
    file_id = target.get("file_id")
    prefix = f"derived/{file_id}" if file_id else f"viewer/{run.id}"
    key = f"{prefix}/captures/{capture_id}"
    get_storage().put_bytes(key, data, mime)
    db.add(
        ViewerCapture(
            id=capture_id,
            run_id=run.id,
            file_id=file_id,
            target=target,
            storage_key=key,
            digest=hashlib.sha256(data).hexdigest(),
            content_type=mime,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    )
    db.flush()
    return {"status": "ready", "capture_id": capture_id, "version": target["version"], "source": target["source"]}


def render_capture(db, run, body, identity):
    # Structural Word paragraphs and spreadsheet cells have no proven physical-page mapping.
    if body.path and body.at:
        from .workspace_documents import request_workspace

        result = request_workspace(db, db.get(User, run.user_id), run.id, body.path, "render", [body.at], body.version)
        if result.get("version") != body.version:
            raise HTTPException(409, "workspace version changed")
        data = base64.b64decode(result["image"], validate=True)
        if len(data) > MAX_IMAGE:
            return {"status": "unavailable", "reason": "render_size_limit"}
        from ..contracts.document_view import RENDER_REVISION

        captured = save(
            db,
            run,
            body.version + RENDER_REVISION + body.at,
            {"run_id": run.id, "path": body.path, "version": body.version, "at": body.at, "source": "render"},
            data,
            result["mime_type"],
        )
        db.commit()
        return captured
    if not body.file_id or not body.at or not re.fullmatch(r"(?:page:|slide:)[1-9]\d*", body.at):
        return {"status": "unavailable", "reason": "precise_render_range_unavailable"}
    from .routes_files import _ready_file, get_preview

    file = _ready_file(db, body.file_id, db.get(User, run.user_id))
    if not (
        (body.at.startswith("page:") and file.filename.lower().endswith(".pdf"))
        or (body.at.startswith("slide:") and file.filename.lower().endswith(".pptx"))
    ):
        return {"status": "unavailable", "reason": "precise_render_range_unavailable"}
    preview = get_preview(file.id, db.get(User, run.user_id), db)
    page = int(body.at.split(":")[1])
    if preview.get("status") != "ready" or not 1 <= page <= len(preview.get("pages", [])):
        return {"status": "unavailable", "reason": "render_range_not_ready"}
    name = preview["pages"][page - 1]
    asset = (file.meta or {}).get("preview_assets", {}).get(name, {})
    storage = get_storage()
    key = asset.get("key")
    if not key or storage.size(key) > MAX_IMAGE:
        return {"status": "unavailable", "reason": "render_size_limit"}
    from ..contracts.document_view import RENDER_REVISION

    result = save(
        db,
        run,
        file.sha256 + RENDER_REVISION + body.at,
        {"file_id": file.id, "version": file.sha256, "at": body.at, "source": "render"},
        storage.get_bytes(key),
        asset.get("content_type", "image/png"),
    )
    db.commit()
    return result


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
    expiry = capture.expires_at.replace(tzinfo=UTC) if capture.expires_at.tzinfo is None else capture.expires_at
    if expiry <= datetime.now(UTC):
        raise HTTPException(410, "capture expired")
    from .routes_document_view import authorize_state

    authorize_state(db, db.get(User, run.user_id), capture.target)
    data = get_storage().get_bytes(capture.storage_key)
    if len(data) > MAX_IMAGE or hashlib.sha256(data).hexdigest() != capture.digest:
        raise HTTPException(410, "capture unavailable")
    return Response(data, media_type=capture.content_type, headers={"Cache-Control": "private, no-store"})


def purge(db, storage, now):
    rows = list(db.scalars(select(ViewerCapture).where(ViewerCapture.expires_at <= now).order_by(ViewerCapture.expires_at).limit(100)))
    for row in rows:
        storage.delete(row.storage_key)
        db.delete(row)
    db.commit()
    return len(rows)
