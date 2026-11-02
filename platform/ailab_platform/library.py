"""Shared ownership and file deletion helpers."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import PurePath

from fastapi import HTTPException
from sqlalchemy import delete, func, insert, literal, literal_column, select, update
from sqlalchemy.orm import Session

from .config import settings
from .models import Artifact, FileObject, Project, User, utcnow
from .naming import name_key, numbered_name
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
    from .api.source_lifecycle import invalidate
    from .models import DocumentBlock, FileDerivation, MessageFileRef, OcrPageResult, RenderedPage

    invalidate(db, files[0].user_id, ids)
    db.execute(update(MessageFileRef).where(MessageFileRef.file_id.in_(ids)).values(filename_snapshot=None, upload_id=None))
    db.execute(delete(RenderedPage).where(RenderedPage.file_id.in_(ids)))
    db.execute(delete(OcrPageResult).where(OcrPageResult.file_id.in_(ids)))
    derivations = select(FileDerivation.id).where(FileDerivation.file_id.in_(ids))
    db.execute(delete(DocumentBlock).where(DocumentBlock.derivation_id.in_(derivations)))
    db.execute(delete(FileDerivation).where(FileDerivation.file_id.in_(ids)))
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
        db.execute(update(FileObject).where(FileObject.id.in_(ids)).values(purged_at=utcnow(), is_head=0, meta={}, filename="文件已删除"))
        return keys
    # Clear links within the selection too, so FK enforcement does not depend on delete order.
    db.execute(update(FileObject).where(FileObject.parent_id.in_(ids)).values(parent_id=None))
    # Keep a byte-free history identity after the file/Artifact FK rows are removed.
    db.execute(
        insert(MessageFileRef).from_select(
            ["id", "run_id", "message_kind", "message_id", "file_id", "role", "ordinal", "reference_origin", "created_at"],
            select(
                Artifact.id,
                Artifact.run_id,
                literal("artifact"),
                Artifact.id,
                Artifact.file_id,
                literal("output"),
                literal(0),
                literal("library"),
                literal(utcnow()),
            ).where(Artifact.file_id.in_(ids)),
        )
    )
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


def reserve_filename(db: Session, file: FileObject) -> None:
    original = file.filename
    for number in range(1, 10002):
        candidate = original if number == 1 else numbered_name(original, number)
        key = name_key(candidate)
        with db.no_autoflush:
            exists = db.scalar(
                select(FileObject.id)
                .where(
                    FileObject.user_id == file.user_id,
                    FileObject.purpose == (file.purpose or "library"),
                    func.coalesce(FileObject.project_id, literal_column("''")) == (file.project_id or ""),
                    FileObject.name_key == key,
                    FileObject.lineage_id != (file.lineage_id or file.id),
                    *live_file_predicates(),
                )
                .limit(1)
            )
        if not exists:
            file.filename, file.name_key = candidate, key
            return
    raise HTTPException(409, "同名文件过多，请换一个文件名")


def assign_file_version(db: Session, file: FileObject, parent: FileObject | None = None) -> None:
    """Allocate under user_critical_section, including branching from an older version."""
    if parent is None:
        file.lineage_id, file.version, file.is_head = file.id, 1, 1
        reserve_filename(db, file)
        return
    if parent.purged_at is not None:
        raise HTTPException(status_code=410, detail="version purged")
    file.purpose = parent.purpose
    file.lineage_id = parent.lineage_id
    file.version = db.scalar(select(func.max(FileObject.version)).where(FileObject.lineage_id == parent.lineage_id)) + 1
    file.parent_id = parent.id
    file.is_head = 1
    if file.project_id is None:
        file.project_id = parent.project_id
    db.execute(
        update(FileObject).where(FileObject.lineage_id == parent.lineage_id, FileObject.is_head == literal_column("1")).values(is_head=0)
    )

    reserve_filename(db, file)


def move_lineage(db: Session, file: FileObject, project_id: str | None) -> None:
    head = db.scalar(select(FileObject).where(FileObject.lineage_id == file.lineage_id, FileObject.is_head == 1))
    if head.purpose != "library":
        raise HTTPException(409, "技能只能在技能页管理")
    head.project_id = project_id
    reserve_filename(db, head)
    db.execute(
        update(FileObject)
        .where(FileObject.lineage_id == head.lineage_id)
        .values(project_id=project_id, filename=head.filename, name_key=head.name_key)
    )
    db.flush()


def trim_file_versions(db: Session, file: FileObject) -> list[str]:
    """Keep history rows and references; remove excess objects only after commit."""
    # Artifact delivery enters the library already scanned/ready, bypassing the upload completion hook.
    from .api.preview_requests import request_ready_preview

    db.flush()
    request_ready_preview(db, file)
    query = select(FileObject).where(FileObject.lineage_id == file.lineage_id, FileObject.purged_at.is_(None))
    if file.purpose == "platform_skill":
        from .models import PlatformSkill, RunAssetRef, SkillDraft, UserPlatformSkill

        # Correlated index probes: do not load every run/subscription/draft in the platform to retain one lineage.
        for source, predicate in (
            (PlatformSkill, PlatformSkill.current_file_id == FileObject.id),
            (UserPlatformSkill, UserPlatformSkill.base_file_id == FileObject.id),
            (RunAssetRef, RunAssetRef.file_id == FileObject.id),
            (SkillDraft, (SkillDraft.base_file_id == FileObject.id) & SkillDraft.status.in_(["preparing", "ready", "failed"])),
        ):
            query = query.where(~select(1).select_from(source).where(predicate).exists())
        query = query.limit(settings.gc_batch_size)
    oldest = list(db.scalars(query.order_by(FileObject.version.desc()).offset(settings.max_file_versions)))
    return delete_file_rows(db, oldest, tombstone=True)


def trash_lineages(db: Session, user_id: str, lineage_ids: list[str], *, project_id: str | None = None) -> None:
    from .api.source_lifecycle import invalidate

    file_ids = list(
        db.scalars(
            select(FileObject.id).where(
                FileObject.user_id == user_id, FileObject.lineage_id.in_(lineage_ids), FileObject.deleted_at.is_(None)
            )
        )
    )
    invalidate(db, user_id, file_ids)
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
    for lineage in lineage_ids:
        head = db.scalar(select(FileObject).where(FileObject.user_id == user_id, FileObject.lineage_id == lineage, FileObject.is_head == 1))
        if not head:
            continue
        reserve_filename(db, head)
        db.execute(
            update(FileObject)
            .where(FileObject.user_id == user_id, FileObject.lineage_id == lineage)
            .values(filename=head.filename, name_key=head.name_key, deleted_at=None, purge_at=None, trashed_from_project_id=None)
        )
        db.flush()


def purge_lineages(db: Session, user_id: str, lineage_ids: list[str]) -> list[str]:
    """Bound row cleanup; callers own the transaction and remove objects after commit."""
    keys = []
    while files := list(
        db.scalars(select(FileObject).where(FileObject.user_id == user_id, FileObject.lineage_id.in_(lineage_ids)).limit(200))
    ):
        keys.extend(delete_file_rows(db, files, detach_children=True))
    return keys
