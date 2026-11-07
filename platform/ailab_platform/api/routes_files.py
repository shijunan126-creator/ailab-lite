"""File library: upload, list, download and presigned-upload compatibility."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import tempfile
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, literal_column, or_, select, tuple_
from sqlalchemy.orm import Session

from ..config import settings
from ..contracts.file_preview import PREVIEW_VERSION, FilePreview
from ..deps import current_user, get_db
from ..library import (
    THUMBNAIL_VERSION,
    assign_file_version,
    delete_storage_objects,
    live_file_predicates,
    move_lineage,
    owned_project,
    purge_lineages,
    restore_lineages,
    schedule_object_deletion,
    trash_lineages,
    trim_file_versions,
)
from ..locks import user_critical_section
from ..models import FileObject, FileStatus, UploadReceipt, User, new_id, utcnow
from ..quota import enforce_library_quota
from ..schemas import FileBatch, FileOut, LibraryFilePatch
from ..storage import get_storage
from .jobs import stage_job
from .serializers import file_dict, file_dicts
from .upload_identity import fingerprint, receipt_for, replay, reusable

router = APIRouter(prefix="/v1/files", tags=["files"])
trash_router = APIRouter(prefix="/v1/trash", tags=["files"])
log = logging.getLogger("ailab.api.files")


class PresignIn(BaseModel):
    client_upload_id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9:_-]+$")
    disposition: Literal["reuse", "copy"] = "reuse"
    size: int | None = Field(default=None, gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    project_id: str | None = None
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = "application/octet-stream"


def safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.replace("\x00", "").strip()
    return (name or "file")[:200]


def content_disposition(filename: str) -> str:
    ascii_name = "".join(c for c in filename if 32 <= ord(c) < 127) or "download"
    ascii_name = ascii_name.replace("\\", "\\\\").replace('"', '\\"')
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _owned_file(db: Session, file_id: str, user: User) -> FileObject:
    file = db.get(FileObject, file_id, populate_existing=True)
    if file is None or file.user_id != user.id or file.purpose in {"brand", "template"}:
        raise HTTPException(status_code=404, detail="file not found")
    return file


@router.post("", response_model=FileOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    upload: UploadFile = File(...),
    parent_id: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    purpose: Literal["library", "skill"] = Query(default="library"),
    client_upload_id: str | None = Query(default=None, min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9:_-]+$"),
    disposition: Literal["reuse", "copy"] = Query(default="reuse"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    # Stream Starlette's spooled upload to a temp file while hashing. Only the
    # chunk and the object-storage copy exist at once, never the whole file twice.
    tmp = await asyncio.to_thread(tempfile.NamedTemporaryFile, prefix="ailab-upload-", delete=False)
    storage = await asyncio.to_thread(get_storage)
    try:
        digest = hashlib.sha256()
        size = 0
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise HTTPException(status_code=413, detail=f"file exceeds {settings.max_upload_bytes} bytes")
            digest.update(chunk)
            await asyncio.to_thread(tmp.write, chunk)
        await asyncio.to_thread(tmp.close)
        if size == 0:
            raise HTTPException(status_code=400, detail="empty file")

        filename = safe_filename(upload.filename or "file")
        content_type = upload.content_type or "application/octet-stream"
        request_hash = fingerprint(
            kind="multipart",
            filename=filename,
            content_type=content_type,
            size=size,
            sha256=digest.hexdigest(),
            project_id=project_id,
            purpose=purpose,
            parent_id=parent_id,
            disposition=disposition,
        )
        file_id = new_id("fil")
        key = f"{'skills' if purpose == 'skill' else 'uploads'}/{user.id}/{file_id}/{filename}"
        try:

            def record() -> dict:
                # The per-user lock can be held for a while by another request of this user (an artifact being
                # hashed): wait for it in a worker thread, never on the event loop that serves everyone.
                with user_critical_section(db, user.id):  # quota check and the file row are one step
                    prior = db.get(UploadReceipt, (user.id, client_upload_id)) if client_upload_id else None
                    if prior:
                        result = replay(db, prior, request_hash)
                        return result
                    if project_id is not None:
                        owned_project(db, project_id, user)
                    match = (
                        reusable(db, user.id, project_id, filename, content_type, size, digest.hexdigest())
                        if (purpose == "library" and parent_id is None and disposition == "reuse")
                        else None
                    )
                    if match:
                        receipt_for(db, user.id, client_upload_id, request_hash, match.id, filename=filename, reused=True)
                        db.commit()
                        return {**file_dict(match), "reused": True}
                    enforce_library_quota(db, user.id, size)
                    parent = _owned_file(db, parent_id, user) if parent_id else None
                    if parent is not None and parent.deleted_at is not None:
                        raise HTTPException(status_code=409, detail="file is trashed")
                    if parent is not None and parent.purpose == "skill":
                        if not parent.is_head or parent.status == FileStatus.PENDING:
                            raise HTTPException(422, "技能已更新或正在扫描，请刷新后重试")
                        if parent.filename.endswith(".zip") and not filename.endswith(".zip"):
                            raise HTTPException(422, "请上传完整技能压缩包，保留附带脚本")
                    # Replays and content matches only hash the incoming spool; they never write another object.
                    storage.put_file(key, tmp.name, content_type)
                    row = FileObject(
                        id=file_id,
                        user_id=user.id,
                        filename=filename,
                        content_type=content_type,
                        size=size,
                        sha256=digest.hexdigest(),
                        storage_key=key,
                        status=FileStatus.PENDING,
                        parent_id=parent_id,
                        project_id=project_id if purpose == "library" else None,
                        purpose=purpose,
                        meta={
                            "processing": "queued",
                            **({"input_lifecycle": 2} if purpose == "library" and not (parent and parent.purpose == "skill") else {}),
                            "upload_filename": filename,
                            "upload_provenance": {"id": new_id("upl")},
                            **(
                                {"skill_claim": (parent.meta or {}).get("skill", {}).get("name")}
                                if parent is not None and parent.purpose == "skill"
                                else {}
                            ),
                        },
                    )
                    assign_file_version(db, row, parent)
                    db.add(row)
                    keys = trim_file_versions(db, row)
                    db.flush()
                    stage_job(db, "file", file_id=row.id)
                    receipt_for(db, user.id, client_upload_id, request_hash, row.id, filename=filename)
                    db.commit()
                    delete_storage_objects(keys)
                    db.refresh(row)
                    return file_dict(row)

            file = await asyncio.to_thread(record)
        except Exception:

            def cleanup_uncommitted():
                # A committed receipt may outlive a failed response serialization. Never delete its object.
                db.rollback()
                with user_critical_section(db, user.id):
                    accepted = db.get(FileObject, file_id, populate_existing=True)
                    if accepted is not None and accepted.storage_key == key:
                        return
                    schedule_object_deletion(db, key, "upload_failed")
                    db.commit()
                delete_storage_objects([key])

            try:
                await asyncio.to_thread(cleanup_uncommitted)
            except Exception:
                log.warning("upload cleanup could not verify commit; retained object for reconciliation", exc_info=True)
            raise
    finally:
        await asyncio.to_thread(tmp.close)
        await asyncio.to_thread(os.unlink, tmp.name)

    return file


@router.get("", response_model=list[FileOut])
def list_files(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    category: Literal["all", "starred", "images", "slides"] = "all",
    trashed: bool = False,
    cursor: str | None = None,
    project_id: str | None = None,
    scope: Literal["all", "root"] = "all",
    q: str | None = None,
    ids: list[str] | None = Query(default=None, max_length=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    # Explicit IDs refer to immutable versions (citations/artifacts), while browsing lists only heads.
    heads = (FileObject.is_head == literal_column("1"),) if ids is None else ()
    visible = (FileObject.deleted_at.is_not(None),) if trashed else (FileObject.deleted_at.is_(None), FileObject.purged_at.is_(None))
    stmt = select(FileObject.id).where(FileObject.user_id == user.id, FileObject.purpose == "library", *heads, *visible)
    if ids is not None:
        stmt = stmt.where(FileObject.id.in_(ids))
    if project_id is not None:
        if not trashed:
            owned_project(db, project_id, user)
        stmt = stmt.where(
            or_(FileObject.project_id == project_id, FileObject.trashed_from_project_id == project_id)
            if trashed
            else FileObject.project_id == project_id
        )
    elif scope == "root":
        stmt = stmt.where(FileObject.project_id.is_(None))
        if trashed:
            stmt = stmt.where(FileObject.trashed_from_project_id.is_(None))
    if category == "starred":
        stmt = stmt.where(FileObject.starred_at.is_not(None))
    elif category == "images":
        # SQLite can match a partial index only with this constant predicate (a bind parameter won't match).
        stmt = stmt.where(FileObject.content_type.like(literal_column("'image/%'")))
    if category == "slides":
        stmt = stmt.where(literal_column("(lower(filename) LIKE '%.pptx' OR lower(filename) LIKE '%.potx')"))
    if q is not None:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(FileObject.filename.ilike(f"%{escaped}%", escape="\\"))
    order = FileObject.deleted_at if trashed else (FileObject.starred_at if category == "starred" else FileObject.created_at)
    if cursor is not None:
        previous = _owned_file(db, cursor, user)
        position = previous.deleted_at if trashed else (previous.starred_at if category == "starred" else previous.created_at)
        if position is None:
            raise HTTPException(status_code=400, detail="invalid file cursor")
        stmt = stmt.where(tuple_(order, FileObject.id) < tuple_(position, previous.id))
    page_ids = list(db.scalars(stmt.order_by(order.desc(), FileObject.id.desc()).limit(limit).offset(offset)))
    by_id = {
        f.id: f
        for f in db.scalars(
            select(FileObject).where(
                FileObject.id.in_(page_ids),
                FileObject.user_id == user.id,
                *heads,
                *visible,
            )
        )
    }
    return file_dicts(db, [by_id[fid] for fid in page_ids if fid in by_id])


@router.get("/folders")
def list_file_folders(
    category: Literal["all", "starred", "images", "slides"] = "all",
    trashed: bool = False,
    q: str = "",
    after: str = "",
    limit: int = Query(100, ge=1, le=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    from ..models import Project

    folder = func.coalesce(FileObject.project_id, FileObject.trashed_from_project_id) if trashed else FileObject.project_id
    predicates = [
        FileObject.user_id == user.id,
        FileObject.purpose == "library",
        FileObject.is_head == literal_column("1"),
        FileObject.purged_at.is_(None),
        folder > after,
    ]
    predicates.append(FileObject.deleted_at.is_not(None) if trashed else FileObject.deleted_at.is_(None))
    if category == "starred":
        predicates.append(FileObject.starred_at.is_not(None))
    if category == "images":
        predicates.append(FileObject.content_type.like(literal_column("'image/%'")))
    if category == "slides":
        predicates.append(literal_column("(lower(filename) LIKE '%.pptx' OR lower(filename) LIKE '%.potx')"))
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        predicates.append(FileObject.filename.ilike(f"%{escaped}%", escape="\\"))
    folder_name = (
        func.coalesce(Project.name, func.max(FileObject.meta["trashed_project_name"].as_string()), "已删除项目")
        if trashed
        else Project.name
    )
    stmt = (
        select(folder.label("id"), folder_name.label("name"), func.count().label("files"))
        .outerjoin(Project, Project.id == folder)
        .where(*predicates)
        .group_by(folder, Project.name)
        .order_by(folder)
        .limit(limit)
    )
    if category == "all" and not trashed and not q:
        nonempty = (
            select(FileObject.id)
            .where(
                FileObject.user_id == user.id, FileObject.project_id == Project.id, FileObject.purpose == "library", *live_file_predicates()
            )
            .exists()
        )
        empty = select(Project.id, Project.name, literal_column("0")).where(Project.user_id == user.id, Project.id > after, ~nonempty)
        stmt = stmt.order_by(None).limit(None).union_all(empty).order_by("id").limit(limit)
    return [{"id": key, "name": name, "files": count} for key, name, count in db.execute(stmt)]


def _owned_files(db: Session, ids: list[str], user: User) -> list[FileObject]:
    unique = list(dict.fromkeys(ids))
    files = db.scalars(
        select(FileObject)
        .where(FileObject.id.in_(unique), FileObject.user_id == user.id, FileObject.purpose.not_in(["brand", "template"]))
        .execution_options(populate_existing=True)
    ).all()
    by_id = {file.id: file for file in files}
    if len(by_id) != len(unique):
        raise HTTPException(status_code=404, detail="file not found")
    return [by_id[file_id] for file_id in unique]


@router.post("/batch")
def batch_files(body: FileBatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    keys = []
    with user_critical_section(db, user.id):
        files = _owned_files(db, body.ids, user)
        if body.action == "move" and body.project_id is not None:
            owned_project(db, body.project_id, user)
        now = utcnow()
        lineages = list({f.lineage_id for f in files})
        if body.action in {"delete", "trash"}:
            trash_lineages(db, user.id, lineages)
        elif body.action == "restore":
            restore_lineages(db, user.id, lineages)
        elif body.action == "purge":
            if any(f.deleted_at is None for f in files):
                raise HTTPException(status_code=409, detail="file is not trashed")
            keys = purge_lineages(db, user.id, lineages)
        else:
            if any(f.deleted_at is not None or f.purged_at is not None for f in files):
                raise HTTPException(status_code=404, detail="file not found")
            if body.action == "move":
                for file in files:
                    move_lineage(db, file, body.project_id)
            for file in db.scalars(select(FileObject).where(FileObject.user_id == user.id, FileObject.lineage_id.in_(lineages))):
                if body.action == "star":
                    file.starred_at = file.starred_at or now
                elif body.action == "unstar":
                    file.starred_at = None

        db.commit()
    delete_storage_objects(keys)
    return {"count": len(files)}


class _ArchiveWriter:
    """A non-seekable ZIP sink drained once per storage chunk."""

    def __init__(self):
        self.buffer = bytearray()

    def write(self, data: bytes) -> int:
        self.buffer.extend(data)
        return len(data)

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        data = bytes(self.buffer)
        self.buffer.clear()
        return data


def _archive_stream(entries: list[tuple[str, str]]) -> Iterator[bytes]:
    writer = _ArchiveWriter()
    used: set[str] = set()
    with zipfile.ZipFile(writer, "w", compression=zipfile.ZIP_STORED) as archive:
        for filename, key in entries:
            name = safe_filename(filename)
            stem, suffix = Path(name).stem, Path(name).suffix
            number = 2
            while name in used:
                name = f"{stem} ({number}){suffix}"
                number += 1
            used.add(name)
            stream = get_storage().stream(key)
            try:
                with archive.open(name, "w", force_zip64=True) as target:
                    yield writer.drain()
                    for chunk in stream:
                        target.write(chunk)
                        yield writer.drain()
                yield writer.drain()
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    close()
    yield writer.drain()


class _ClosingStreamingResponse(StreamingResponse):
    def __init__(self, stream: Iterator[bytes], **kwargs):
        self._stream = stream
        super().__init__(stream, **kwargs)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Starlette does not close sync iterators on disconnect. Shield storage cleanup from cancellation.
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self._stream.close)


@router.get("/archive")
def download_archive(
    ids: list[str] = Query(min_length=1, max_length=200),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    files = _owned_files(db, ids, user)
    for file in files:
        if file.deleted_at is not None:
            raise HTTPException(status_code=404, detail="file not found")
        if file.purged_at is not None:
            raise HTTPException(status_code=410, detail="version purged")
        if file.status != FileStatus.READY:
            raise HTTPException(status_code=409, detail=f"file {file.filename} is {file.status}, not downloadable")
    if sum(file.size for file in files) > settings.max_archive_bytes:
        raise HTTPException(status_code=413, detail=f"archive exceeds {settings.max_archive_bytes} bytes")
    entries = [(file.filename, file.storage_key) for file in files]
    db.close()
    filename = f"ailab-files-{datetime.now(UTC):%Y%m%d}.zip"
    return _ClosingStreamingResponse(
        _archive_stream(entries),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.get("/{file_id}/thumbnail")
def download_thumbnail(
    file_id: str,
    if_none_match: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    file = _owned_file(db, file_id, user)
    key = (file.meta or {}).get("thumb_key")
    if file.status != FileStatus.READY or file.purged_at is not None or not key:
        raise HTTPException(status_code=404, detail="thumbnail not found")
    etag = '"' + hashlib.sha256(f"{file.sha256}:{THUMBNAIL_VERSION}".encode()).hexdigest() + '"'
    headers = {"Cache-Control": "private, max-age=31536000, immutable", "ETag": etag}
    db.close()
    if if_none_match and any(value.strip().removeprefix("W/") in (etag, "*") for value in if_none_match.split(",")):
        return Response(status_code=304, headers=headers)
    return _ClosingStreamingResponse(get_storage().stream(key), media_type="image/webp", headers=headers)


def _ready_file(db: Session, file_id: str, user: User) -> FileObject:
    file = _owned_file(db, file_id, user)
    if file.purged_at is not None:
        raise HTTPException(status_code=410, detail="version purged")
    scanned = (
        file.status == FileStatus.PENDING
        and (file.meta or {}).get("input_lifecycle") != 2
        and file.purpose == "library"
        and ((file.meta or {}).get("scan") or {}).get("verdict") == "clean"
        and bool((file.meta or {}).get("preview_job_id"))
    )
    if not (file.status == FileStatus.READY or scanned):
        raise HTTPException(status_code=404, detail="preview not found")
    return file


@router.get("/{file_id}/preview", response_model=FilePreview, response_model_exclude_unset=True)
def get_preview(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    file = _ready_file(db, file_id, user)
    cached = (file.meta or {}).get("preview")
    now = datetime.now(UTC).timestamp()
    if cached and cached.get("reason") == "text_unavailable":
        cached = None
    if cached and (cached["status"] != "pending" or now - (file.meta or {}).get("preview_requested_at", 0) <= 300):
        return cached
    if cached and cached.get("status") == "pending":
        from ..models import OutboxMessage

        if (file.meta or {}).get("preview_job_id") and db.get(OutboxMessage, file.meta["preview_job_id"]):
            return cached
    # Same user lock as deletion: concurrent opens use the committed manifest, without storage reads.
    with user_critical_section(db, user.id):
        file = _ready_file(db, file_id, user)
        meta = dict(file.meta or {})
        cached = meta.get("preview")
        if cached and cached.get("reason") == "text_unavailable":
            cached = None
        if cached and (cached["status"] != "pending" or now - meta.get("preview_requested_at", 0) <= 300):
            return cached
        from .preview_requests import request_preview

        manifest, job_id = request_preview(db, file)
        db.commit()
        if job_id:
            from .preview_dispatch import dispatch_preview_jobs

            try:
                dispatch_preview_jobs(job_id)
            except Exception:
                log.warning("preview request retained in outbox", exc_info=True)
        return manifest


@router.get("/{file_id}/preview/{name}")
def preview_asset(
    file_id: str,
    name: str,
    if_none_match: str | None = Header(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    file = _ready_file(db, file_id, user)
    meta = file.meta or {}
    manifest = meta.get("preview") or {}
    allowed = [*manifest.get("pages", []), manifest.get("data"), manifest.get("image"), manifest.get("text")]
    if manifest.get("status") not in {"ready", "pending"} or name not in allowed or name in {".", ".."} or "/" in name or "\\" in name:
        raise HTTPException(status_code=404, detail="preview asset not found")
    asset = (meta.get("preview_assets") or {}).get(name) or {}
    if name == "original":
        key, mime, digest = file.storage_key, "image/svg+xml", file.sha256
    elif name == "display.webp":
        key, mime, digest = meta.get("display_key"), "image/webp", asset.get("sha256") or file.sha256
    elif name == "text.txt":
        key, mime, digest = meta.get("text_key") or asset.get("key"), "text/plain; charset=utf-8", asset.get("sha256") or file.sha256
    else:
        key, mime, digest = asset.get("key"), asset.get("content_type"), asset.get("sha256")
    if not key or not digest:
        raise HTTPException(status_code=404, detail="preview asset not found")
    etag = '"' + hashlib.sha256(f"{digest}:{PREVIEW_VERSION}:{name}".encode()).hexdigest() + '"'
    headers = {
        "Cache-Control": "private, max-age=31536000, immutable",
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox",
    }
    db.close()
    if if_none_match and any(v.strip().removeprefix("W/") in (etag, "*") for v in if_none_match.split(",")):
        return Response(status_code=304, headers=headers)
    return _ClosingStreamingResponse(get_storage().stream(key), media_type=mime, headers=headers)


@router.patch("/{file_id}", response_model=FileOut)
def patch_file(file_id: str, body: LibraryFilePatch, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    with user_critical_section(db, user.id):
        file = _owned_file(db, file_id, user)
        if file.deleted_at is not None or file.purged_at is not None:
            raise HTTPException(status_code=404, detail="file not found")
        lineage = list(db.scalars(select(FileObject).where(FileObject.lineage_id == file.lineage_id)))
        if "project_id" in body.model_fields_set:
            if body.project_id is not None:
                owned_project(db, body.project_id, user)
            move_lineage(db, file, body.project_id)
        if "starred" in body.model_fields_set:
            for version in lineage:
                version.starred_at = (version.starred_at or utcnow()) if body.starred else None
        db.commit()
        return file_dict(file)


@router.get("/{file_id}", response_model=FileOut)
def get_file(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return file_dict(_owned_file(db, file_id, user))


@router.get("/{file_id}/content")
def download_file(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    file = _owned_file(db, file_id, user)
    if file.purged_at is not None:
        raise HTTPException(status_code=410, detail="version purged")
    if file.status != FileStatus.READY:
        raise HTTPException(status_code=409, detail=f"file is {file.status}, not downloadable")
    # Do not pin the request's DB connection for the duration of the download:
    # the stream reads object storage only. Closing here is safe (the dependency
    # close is idempotent).
    db.close()
    return _ClosingStreamingResponse(
        get_storage().stream(file.storage_key),
        media_type=file.content_type,
        headers={
            "Content-Disposition": content_disposition(file.filename),
            "Content-Length": str(file.size),
        },
    )


@router.delete("/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_file(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> Response:
    """Trash the whole lineage, retaining bytes, quota and historical references."""
    with user_critical_section(db, user.id):
        file = _owned_file(db, file_id, user)
        trash_lineages(db, user.id, [file.lineage_id])
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/presign", status_code=200)
def presign_upload(body: PresignIn, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    """A reservation is an operation, not a bindable attachment until completion."""
    storage = get_storage()
    if body.client_upload_id and (body.size is None or body.sha256 is None):
        raise HTTPException(422, "预签名上传操作必须提供size和sha256")
    if body.size is not None and body.size > settings.max_upload_bytes:
        raise HTTPException(413, "file too large")
    filename = safe_filename(body.filename)
    digest = fingerprint(
        kind="presign",
        filename=filename,
        content_type=body.content_type,
        project_id=body.project_id,
        disposition=body.disposition,
        size=body.size,
        sha256=body.sha256,
    )
    with user_critical_section(db, user.id):
        prior = db.get(UploadReceipt, (user.id, body.client_upload_id)) if body.client_upload_id else None
        if prior:
            if prior.fingerprint != digest:
                raise HTTPException(422, "上传操作标识已用于其他内容")
            if prior.state == "completed":
                return {"file_id": prior.file_id, "completed": True, "file": replay(db, prior, digest)}
            file = db.get(FileObject, prior.reservation_id, populate_existing=True)
            if file is None:
                raise HTTPException(410, "上传预约已失效")
            if prior.state != "reserved" or file.deleted_at is not None or file.purged_at is not None:
                raise HTTPException(410, "上传预约已失效")
            if (utcnow().timestamp() - file.created_at.replace(tzinfo=UTC).timestamp()) > 900:
                raise HTTPException(410, "上传预约已过期，请重新选择文件")
        else:
            if body.project_id is not None:
                owned_project(db, body.project_id, user)
            file_id = new_id("fil")
            file = FileObject(
                id=file_id,
                user_id=user.id,
                project_id=body.project_id,
                filename=filename,
                content_type=body.content_type,
                storage_key=f"uploads/{user.id}/{file_id}/{filename}",
                status=FileStatus.PENDING,
                meta={
                    "processing": "awaiting_upload",
                    "input_lifecycle": 2,
                    "disposition": body.disposition,
                    "upload_filename": filename,
                    "expected_size": body.size,
                    "expected_sha256": body.sha256,
                },
            )
            assign_file_version(db, file)
            db.add(file)
            receipt_for(db, user.id, body.client_upload_id, digest, file.id, filename=filename, reservation_id=file.id, state="reserved")
        url = storage.presign_put(file.storage_key, expires=900)
        if url is None:
            raise HTTPException(501, "storage backend does not support presigned upload; use POST /v1/files")
        db.commit()
        return {"file_id": file.id, "upload_url": url, "method": "PUT", "expires_in": 900, "completed": False}


@router.post("/{file_id}/complete", response_model=FileOut)
def complete_upload(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    storage = get_storage()
    with user_critical_section(db, user.id):
        receipt = db.scalar(select(UploadReceipt).where(UploadReceipt.reservation_id == file_id, UploadReceipt.user_id == user.id).limit(1))
        if receipt and receipt.state == "completed":
            return replay(db, receipt, receipt.fingerprint)
        file = db.get(FileObject, file_id, populate_existing=True)
        if file is None or file.user_id != user.id:
            raise HTTPException(410 if receipt else 404, "原上传文件已删除")
        meta = dict(file.meta or {})
        if meta.get("canonical_file_id"):
            canonical = db.get(FileObject, meta["canonical_file_id"], populate_existing=True)
            if canonical is None or canonical.user_id != user.id or canonical.deleted_at is not None or canonical.purged_at is not None:
                raise HTTPException(410, "原上传文件已删除")
            return {**file_dict(canonical), "reused": True}
        if file.deleted_at is not None or file.purged_at is not None:
            raise HTTPException(410, "原上传文件已删除")
        if meta.get("processing") != "awaiting_upload":
            if meta.get("upload_completed"):
                return file_dict(file)
            raise HTTPException(409, f"file already {file.status}")
        signed_key = file.storage_key
        if not storage.exists(signed_key):
            raise HTTPException(409, "object not uploaded yet")
        final_key = f"uploads/{user.id}/{file.id}/{new_id('obj')}/{file.filename}"
        try:
            storage.copy(signed_key, final_key)
            size = storage.size(final_key)
            if not 0 < size <= settings.max_upload_bytes:
                raise HTTPException(400 if size == 0 else 413, "empty file" if size == 0 else "file too large")
            digest = hashlib.sha256()
            stream = storage.stream(final_key)
            try:
                for chunk in stream:
                    digest.update(chunk)
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()
            if (meta.get("expected_size") is not None and meta["expected_size"] != size) or (
                meta.get("expected_sha256") is not None and meta["expected_sha256"] != digest.hexdigest()
            ):
                raise HTTPException(400, "上传内容与预约不一致")
            filename = meta.get("upload_filename", file.filename)
            match = (
                reusable(db, user.id, file.project_id, filename, file.content_type, size, digest.hexdigest())
                if (meta.get("disposition", "reuse") == "reuse")
                else None
            )
            if match:
                result = {**file_dict(match), "reused": True}
                if receipt:
                    receipt.file_id, receipt.state, receipt.reused = match.id, "completed", 1
                    db.delete(file)  # the receipt owns the alias; an unbound reservation is never a library item
                else:
                    # Old clients have no receipt: retain only a hidden identity tombstone for complete retries.
                    file.deleted_at, file.purged_at, file.is_head = utcnow(), utcnow(), 0
                    file.filename = ""
                    file.meta = {"processing": "reused", "canonical_file_id": match.id}
                cleanup = [signed_key, final_key]
            else:
                enforce_library_quota(db, user.id, size)
                file.storage_key, file.size, file.sha256 = final_key, size, digest.hexdigest()
                file.meta = {**meta, "processing": "queued", "upload_completed": True, "upload_provenance": {"id": new_id("upl")}}
                stage_job(db, "file", file_id=file.id)
                if receipt:
                    receipt.state = "completed"
                result = None
                cleanup = [signed_key]
            for key in cleanup:
                schedule_object_deletion(db, key, "upload_complete")
            db.commit()
        except Exception as exc:
            db.rollback()
            delete_storage_objects([final_key])
            if isinstance(exc, HTTPException) and exc.status_code in (400, 413):
                schedule_object_deletion(db, signed_key, "upload_rejected")
                if receipt:
                    receipt.state, receipt.filename = "rejected", None
                db.delete(file)
                db.commit()
                delete_storage_objects([signed_key])
            raise
        delete_storage_objects(cleanup)
        return result or file_dict(file)


@router.get("/{file_id}/versions", response_model=list[FileOut])
def file_versions(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    file = _owned_file(db, file_id, user)
    files = list(
        db.scalars(
            select(FileObject)
            .where(FileObject.user_id == user.id, FileObject.lineage_id == file.lineage_id)
            .order_by(FileObject.version.desc())
        )
    )
    return file_dicts(db, files)


@router.post("/{file_id}/restore", response_model=FileOut)
def restore_file(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    with user_critical_section(db, user.id):
        file = _owned_file(db, file_id, user)
        restore_lineages(db, user.id, [file.lineage_id])
        db.commit()
        head = db.scalar(select(FileObject).where(FileObject.lineage_id == file.lineage_id, FileObject.is_head == literal_column("1")))
        return file_dict(head)


@router.post("/{file_id}/restore-version", response_model=FileOut, status_code=201)
def restore_version(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    storage = get_storage()
    copied = []
    with user_critical_section(db, user.id):
        old = _owned_file(db, file_id, user)
        if old.purged_at is not None:
            raise HTTPException(status_code=410, detail="version purged")
        if old.is_head:
            raise HTTPException(status_code=409, detail="only an older version can be restored")
        if not storage.exists(old.storage_key):
            raise HTTPException(status_code=409, detail="version object is not available")
        enforce_library_quota(db, user.id, old.size)
        fid = new_id("fil")
        key = f"versions/{user.id}/{fid}/{old.filename}"
        meta = dict(old.meta or {})
        head = db.scalar(select(FileObject).where(FileObject.lineage_id == old.lineage_id, FileObject.is_head == literal_column("1")))
        file = FileObject(
            id=fid,
            user_id=user.id,
            project_id=head.project_id,
            starred_at=head.starred_at,
            filename=old.filename,
            content_type=old.content_type,
            size=old.size,
            sha256=old.sha256,
            storage_key=key,
            status=old.status,
            deleted_at=head.deleted_at,
            purge_at=head.purge_at,
            trashed_from_project_id=head.trashed_from_project_id,
        )
        try:
            copied.append(key)
            storage.copy(old.storage_key, key)
            # Only immutable source/safety facts survive a new file identity.
            # Copying derived bytes would bypass reservations and retain stale
            # index/job identities; consumption prepares them under the new ID.
            meta = {
                k: v
                for k, v in meta.items()
                if k in {"scan", "safety", "scan_exhausted", "safety_reason", "upload_completed", "upload_filename", "disposition"}
            }
            file.meta = {**meta, "input_lifecycle": 2, "restored_from_id": old.id}
            assign_file_version(db, file, old)
            db.add(file)
            keys = trim_file_versions(db, file)
            from ..library import resume_file_processing

            resume_file_processing(db, file)
            db.commit()
        except Exception:
            db.rollback()
            delete_storage_objects(copied)
            raise
    delete_storage_objects(keys)
    return file_dict(file)


@trash_router.delete("")
def empty_trash(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    count = 0
    cutoff = utcnow()
    while True:
        with user_critical_section(db, user.id):
            lineages = list(
                db.scalars(
                    select(FileObject.lineage_id)
                    .where(
                        FileObject.user_id == user.id,
                        FileObject.is_head == literal_column("1"),
                        FileObject.deleted_at.is_not(None),
                        FileObject.deleted_at <= cutoff,
                    )
                    .order_by(FileObject.deleted_at, FileObject.id)
                    .limit(200)
                )
            )
            if not lineages:
                db.commit()
                break
            keys = purge_lineages(db, user.id, lineages)
            db.commit()
        delete_storage_objects(keys)
        count += len(lineages)
    return {"count": count}


@router.get("/{file_id}/document")
def get_document(file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db), structure: bool = False):
    from ..contracts.document_view import PARSER_REVISION
    from .document_view import index

    file = _owned_file(db, file_id, user)
    if file.deleted_at or file.purged_at or file.purpose != "library":
        raise HTTPException(404, "document unavailable")
    if file.status != FileStatus.READY:
        return {"status": "pending" if file.status == FileStatus.PENDING else "failed", "file_id": file.id, "native": False}
    if not structure:
        from ..contracts.document_view import opening_metadata

        if (file.meta or {}).get("scan", {}).get("verdict") != "clean":
            raise HTTPException(409, "document_not_safe")
        return opening_metadata(version=file.sha256, filename=file.filename, size=file.size, file_id=file.id)
    result = index(file)
    if result["status"] == "pending" or (result["status"] == "failed" and not result.get("reason")):
        with user_critical_section(db, user.id):
            job_id = "viewer_" + hashlib.sha256((file.id + file.sha256 + PARSER_REVISION).encode()).hexdigest()[:32]
            stage_job(db, "viewer", file_id=file.id, job_id=job_id)
            db.commit()
        return {**result, "status": "pending"}
    return result
