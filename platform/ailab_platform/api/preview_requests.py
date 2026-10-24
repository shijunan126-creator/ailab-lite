"""API-owned preview requests; callers hold the file owner's lock and commit the outbox atomically."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..contracts.file_preview import preview_kind, text_manifest
from .jobs import stage_job


def request_preview(db, file, *, retry_stale=True):
    meta = dict(file.meta or {})
    now = datetime.now(UTC).timestamp()
    cached = meta.get("preview")
    if cached and cached.get("reason") != "text_unavailable":
        if not retry_stale or cached["status"] != "pending" or now - meta.get("preview_requested_at", 0) <= 300:
            return cached, None
    job_id = None
    kind = "image" if meta.get("display_key") else preview_kind(file.filename, file.content_type)
    suffix = Path(file.filename).suffix.lower()
    if kind is None:
        manifest = {"status": "unsupported", "kind": None}
    elif kind == "text":
        manifest = text_manifest(file.filename, meta) if meta.get("text_key") else {"status": "pending", "kind": "text"}
    elif kind == "image" and (suffix == ".svg" or file.content_type.split(";")[0] == "image/svg+xml"):
        manifest = {"status": "ready", "kind": "image", "image": "original"}
    elif kind == "image" and meta.get("display_key"):
        manifest = {"status": "ready", "kind": "image", "image": "display.webp"}
    else:
        manifest = {"status": "pending", "kind": kind}
        job_id = uuid.uuid4().hex
        meta.update(preview_requested_at=now, preview_job_id=job_id)
        stage_job(db, "preview", file_id=file.id, job_id=job_id)
    if manifest["status"] == "pending" and job_id is None:
        job_id = uuid.uuid4().hex
        meta.update(preview_requested_at=now, preview_job_id=job_id)
        stage_job(db, "preview", file_id=file.id, job_id=job_id)
    meta["preview"] = manifest
    file.meta = meta
    return manifest, job_id
