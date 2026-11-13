"""API-owned preview requests; callers hold the file owner's lock and commit the outbox atomically."""

import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..config import settings
from ..contracts.file_preview import LOCAL_SHEETS, PAGES, SANDBOX_SHEETS, cached_page_preview, preview_kind, text_manifest
from .jobs import stage_job


def request_template_preview(db, file):
    from fastapi import HTTPException

    # The sketch remains usable while the bounded heavy lane is full. A later
    # explicit template open can retry admission without redoing its analysis.
    try:
        with db.begin_nested():
            request_preview(db, file, retry_stale=False)
    except HTTPException as exc:
        if exc.status_code != 429:
            raise


def request_ready_preview(db, file):
    """Speculate only on bounded library documents; the caller commits readiness and outbox together."""
    if (
        file.purpose != "library"
        or (file.meta or {}).get("input_lifecycle") == 2
        or not (file.status == "ready" or (file.status == "pending" and (file.meta or {}).get("scan", {}).get("verdict") == "clean"))
        or file.deleted_at
        or file.purged_at
        or not settings.preview_eager_max_bytes
        or file.size > settings.preview_eager_max_bytes
    ):
        return
    meta = file.meta or {}
    # A durable preview already in the outbox owns the pending state, even if
    # capacity waits exceed the old client retry interval.
    if (meta.get("preview") or {}).get("status") == "pending" and meta.get("preview_job_id"):
        return
    suffix = Path(file.filename).suffix.lower()
    # Native viewers draw originals in the browser; export only on explicit fallback.
    if suffix in {".docx", ".xlsx", ".pptx", ".pdf"} or file.content_type.split(";")[0] == "application/pdf":
        return
    if suffix in PAGES | LOCAL_SHEETS | SANDBOX_SHEETS or file.content_type.split(";")[0] == "application/pdf":
        request_preview(db, file, retry_stale=False)


def legacy_preview_scan_required(file):
    meta = file.meta or {}
    return file.status == "ready" and meta.get("source") == "sandbox" and not (meta.get("scan") or {}).get("verdict")


def request_preview(db, file, *, retry_stale=True, run=None, preparation=None):
    meta = dict(file.meta or {})
    if legacy_preview_scan_required(file):
        job = stage_job(db, "file", file_id=file.id)
        manifest = {"status": "pending", "kind": preview_kind(file.filename, file.content_type)}
        file.status = "pending"
        file.meta = {
            **meta,
            "preview_rescan": True,
            "processing": "queued",
            "processing_job_id": job.id,
            "preview": manifest,
        }
        return manifest, job.id
    if meta.get("preview_rescan"):
        return meta["preview"], None
    now = datetime.now(UTC).timestamp()
    cached = meta.get("preview")
    if cached and cached.get("status") == "pending" and meta.get("preview_job_id"):
        from ..models import OutboxMessage
        from .file_work import register

        existing_job = db.get(OutboxMessage, meta["preview_job_id"])
        if existing_job:
            register(db, file, existing_job, run=run, preparation=preparation)
    if cached and cached.get("reason") != "text_unavailable":
        if not retry_stale or cached["status"] != "pending" or now - meta.get("preview_requested_at", 0) <= 300:
            return cached, None
        from ..models import OutboxMessage

        if meta.get("preview_job_id") and db.get(OutboxMessage, meta["preview_job_id"]):
            return cached, None
    job_id = None
    kind = "image" if meta.get("display_key") else preview_kind(file.filename, file.content_type)
    suffix = Path(file.filename).suffix.lower()
    reused = cached_page_preview(file.id, meta) if kind == "pages" and file.purpose != "template" else None
    if reused:
        manifest, assets = reused
        file.meta = {**meta, "preview": manifest, "preview_assets": assets}
        return manifest, None
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
        meta.pop("preview_requested_pages", None)
        job_id = uuid.uuid4().hex
        meta.update(preview_requested_at=now, preview_job_id=job_id)
        stage_job(db, "preview", file_id=file.id, job_id=job_id)
    if manifest["status"] == "pending" and job_id is None:
        job_id = uuid.uuid4().hex
        meta.update(preview_requested_at=now, preview_job_id=job_id)
        stage_job(db, "preview", file_id=file.id, job_id=job_id)
    meta["preview"] = manifest
    file.meta = meta
    if job_id:
        from ..models import OutboxMessage
        from .file_work import register

        db.flush()
        register(db, file, db.get(OutboxMessage, job_id), run=run, preparation=preparation)
    return manifest, job_id
