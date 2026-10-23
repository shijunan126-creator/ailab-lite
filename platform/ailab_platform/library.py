"""Shared ownership and file deletion helpers."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import PurePath

from fastapi import HTTPException
from sqlalchemy import delete, func, literal_column, select, update
from sqlalchemy.orm import Session

from .config import settings
from .models import Artifact, FileObject, Project, User, utcnow
from .storage import get_storage

log = logging.getLogger("ailab.library")
THUMBNAIL_VERSION = "webp-480-v1"
RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".ico", ".avif"}


def thumbnail_candidate(filename: str, content_type: str) -> bool:
    """Cheap enqueue hint only; untrusted format detection belongs to the Worker."""
    mime = content_type.split(";", 1)[0].lower()
    suffix = PurePath(filename).suffix.lower()
    return mime != "image/svg+xml" and suffix != ".svg" and (mime.startswith("image/") or suffix in RASTER_SUFFIXES)


def owned_project(db: Session, project_id: str, user: User) -> Project:
    project = db.get(Project, project_id, populate_existing=True)
    if project is None or project.user_id != user.id:
        raise HTTPException(status_code=404, detail="project not found")
    return project


def delete_file_rows(db: Session, files: list[FileObject], *, detach_children: bool = False, tombstone: bool = False) -> list[str]:
    """Delete the ledger in this transaction; return objects to remove after commit."""
    ids = [file.id for file in files]
    if not ids:
        return []
    if not detach_children and not tombstone:
        outside = list(db.scalars(select(FileObject.filename).where(FileObject.parent_id.in_(ids), FileObject.id.not_in(ids))))
        if outside:
            raise HTTPException(status_code=409, detail=f"file has newer versions; delete those first: {', '.join(outside)}")
    keys = []
    for file in files:
        meta = file.meta or {}
        keys.extend(
            filter(None, (file.storage_key, meta.get("derived_key"), meta.get("text_key"), meta.get("thumb_key"), meta.get("display_key")))
        )
        keys.append(f"derived/{file.id}/")
    # Deleted now if possible (delete_storage_objects after commit); if that fails, the lifecycle job retries.
    for key in dict.fromkeys(keys):
        schedule_object_deletion(db, key, "file", prefix=key.endswith("/"))
    if tombstone:
        db.execute(update(FileObject).where(FileObject.id.in_(ids)).values(purged_at=utcnow(), is_head=0))
        return keys
    # Clear links within the selection too, so FK enforcement does not depend on delete order.
    db.execute(update(FileObject).where(FileObject.parent_id.in_(ids)).values(parent_id=None))
    db.execute(delete(Artifact).where(Artifact.file_id.in_(ids)))
    db.execute(delete(FileObject).where(FileObject.id.in_(ids)))
    return keys


def schedule_object_deletion(db: Session, key: str, kind: str, *, prefix: bool = False) -> None:
    """A durable intent to remove an object, committed with the change that orphans it; the lifecycle job retries it."""
    from .models import LifecycleDeletion

    if db.scalar(select(LifecycleDeletion.id).where(LifecycleDeletion.key == key)) is None:
        db.add(LifecycleDeletion(key=key, kind=kind, is_prefix=int(prefix)))
        db.flush()


def delete_storage_objects(keys: list[str]) -> None:
    storage = get_storage()
    for key in dict.fromkeys(keys):
        try:
            if key.endswith("/"):
                storage.delete_prefix(key)  # every page, not only the first
            else:
                storage.delete(key)
        except Exception:
            log.warning("could not delete object %s", key, exc_info=True)


def live_file_predicates():
    return (FileObject.is_head == literal_column("1"), FileObject.deleted_at.is_(None), FileObject.purged_at.is_(None))


def assign_file_version(db: Session, file: FileObject, parent: FileObject | None = None) -> None:
    """Allocate under user_critical_section, including branching from an older version."""
    if parent is None:
        file.lineage_id, file.version, file.is_head = file.id, 1, 1
        return
    if parent.purged_at is not None:
        raise HTTPException(status_code=410, detail="version purged")
    file.lineage_id = parent.lineage_id
    file.version = db.scalar(select(func.max(FileObject.version)).where(FileObject.lineage_id == parent.lineage_id)) + 1
    file.parent_id = parent.id
    file.is_head = 1
    if file.project_id is None:
        file.project_id = parent.project_id
    db.execute(
        update(FileObject).where(FileObject.lineage_id == parent.lineage_id, FileObject.is_head == literal_column("1")).values(is_head=0)
    )


def trim_file_versions(db: Session, file: FileObject) -> list[str]:
    """Keep history rows and references; remove excess objects only after commit."""
    db.flush()
    oldest = list(
        db.scalars(
            select(FileObject)
            .where(FileObject.lineage_id == file.lineage_id, FileObject.purged_at.is_(None))
            .order_by(FileObject.version.desc())
            .offset(settings.max_file_versions)
        )
    )
    return delete_file_rows(db, oldest, tombstone=True)


def trash_lineages(db: Session, user_id: str, lineage_ids: list[str], *, project_id: str | None = None) -> None:
    now = utcnow()
    values = {"deleted_at": now, "purge_at": now + timedelta(days=settings.trash_days)}
    if project_id is not None:
        values.update(project_id=None, trashed_from_project_id=project_id)
    db.execute(
        update(FileObject)
        .where(FileObject.user_id == user_id, FileObject.lineage_id.in_(lineage_ids), FileObject.deleted_at.is_(None))
        .values(**values)
    )
    if project_id is not None:
        # Already-trashed versions must lose the FK too when their project is removed.
        db.execute(
            update(FileObject)
            .where(FileObject.user_id == user_id, FileObject.lineage_id.in_(lineage_ids))
            .values(project_id=None, trashed_from_project_id=project_id)
        )


def restore_lineages(db: Session, user_id: str, lineage_ids: list[str]) -> None:
    # Normal trash retains its project FK; project deletion cleared it transactionally.
    db.execute(
        update(FileObject)
        .where(FileObject.user_id == user_id, FileObject.lineage_id.in_(lineage_ids))
        .values(deleted_at=None, purge_at=None, trashed_from_project_id=None)
    )


def purge_lineages(db: Session, user_id: str, lineage_ids: list[str]) -> list[str]:
    """Bound row cleanup; callers own the transaction and remove objects after commit."""
    keys = []
    while files := list(
        db.scalars(select(FileObject).where(FileObject.user_id == user_id, FileObject.lineage_id.in_(lineage_ids)).limit(200))
    ):
        keys.extend(delete_file_rows(db, files, detach_children=True))
    return keys
