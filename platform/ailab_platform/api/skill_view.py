"""Read-only directory view of a skill: built-in folders from the image, md files, and Worker-unpacked zips.

A zip skill is untrusted, so the API never opens it. The first view stages one `skill_view` job; the Worker
unpacks it in its limited child, writes each file to `derived/{file_id}/skill/{sha256}` under derived-asset
grants, and reports the manifest here, where it is recorded on the immutable file row as `meta["skill_view"]`.
"""

from __future__ import annotations

import os
import stat
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import skillfiles
from ..deps import get_db
from ..locks import user_critical_section
from ..models import DerivedAsset, FileObject, FileStatus, OutboxMessage
from ..service_auth import require_scope
from ..skill_drafts.files import checked_path, inspect_file, validate_manifest

# Content is immutable, so only a failure that is not about the archive itself is worth another attempt;
# waiting between attempts bounds a broken dependency to one job per file per interval however often the page polls.
RETRY_SECONDS = 60
ERRORS = {
    "input_limit": ("技能压缩包超过 10 MB 限制", False),
    "invalid_archive": ("技能压缩包无法展开", False),
    "storage_unavailable": ("技能文件暂时无法展开，请稍后重试", True),
    "job_expired": ("技能文件暂时无法展开，请稍后重试", True),
}


def failed(code: str) -> dict:
    message, retryable = ERRORS[code]
    view = {"status": "failed", "error": message, "reason": code}
    if retryable:
        view["retry_at"] = (datetime.now(UTC) + timedelta(seconds=RETRY_SECONDS)).isoformat()
    return view


def _needs_job(db: Session, view: dict) -> bool:
    status = view.get("status")
    if status == "ready":
        return False
    if status == "preparing":
        # The job row disappears only with a recorded outcome, or when the file left the trash meanwhile.
        return db.get(OutboxMessage, view.get("job_id"), populate_existing=True) is None
    if status == "failed":
        return bool(view.get("retry_at")) and datetime.fromisoformat(view["retry_at"]) <= datetime.now(UTC)
    return True  # never unpacked, or a collected cache object invalidated the manifest


def zip_view(db: Session, file: FileObject) -> dict:
    """The recorded view, staging at most one live unpack job per file."""
    from .jobs import stage_job

    if not _needs_job(db, (file.meta or {}).get("skill_view") or {}):
        return file.meta["skill_view"]
    with user_critical_section(db, file.user_id):
        db.refresh(file)
        view = (file.meta or {}).get("skill_view") or {}
        if _needs_job(db, view):
            job = stage_job(db, "skill_view", file_id=file.id)
            view = {"status": "preparing", "job_id": job.id}
            file.meta = {**(file.meta or {}), "skill_view": view}  # a new dict: SQLAlchemy sees the change
        db.commit()
    return view


@lru_cache(maxsize=32)
def _builtin(name: str, version: str) -> tuple[dict, ...]:
    """Trusted image content; the catalogue is fixed per process, so the version only guards a reload."""
    from ..agent.prompts import skills

    root = skills()[name]["path"].parent.resolve()
    items = []
    for current, dirs, names in os.walk(root):  # never descends into symlinked directories
        dirs.sort()
        for entry in sorted(names):
            path = Path(current) / entry
            relative = path.relative_to(root).as_posix()
            try:
                checked_path(relative)
            except skillfiles.SkillFileError:
                continue
            if not stat.S_ISREG(path.lstat().st_mode):
                continue
            if len(items) >= skillfiles.MAX_ENTRIES:
                raise HTTPException(422, "技能文件数量超过限制")
            digest, size, kind = inspect_file(relative, path)
            items.append({"path": relative, "size": size, "kind": kind, "sha256": digest, "location": path})
    return tuple(sorted(items, key=lambda item: item["path"]))


def builtin_files(name: str) -> tuple[dict, ...]:
    from ..agent.prompts import skills

    return _builtin(name, skills()[name].get("sha256") or "")


def builtin_location(name: str, item: dict) -> Path:
    from ..agent.prompts import skills

    root = skills()[name]["path"].parent.resolve()
    location = item["location"]
    # The listing was taken once; re-check that nothing on disk now redirects the path out of the skill.
    if location.resolve() != location or not location.is_relative_to(root) or not stat.S_ISREG(location.lstat().st_mode):
        raise HTTPException(404, "文件不存在")
    return location


router = APIRouter(prefix="/internal/skill-views", dependencies=[Depends(require_scope("jobs:file"))])


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(max_length=40)
    token: str = Field(max_length=40)


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=1024)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str = Field(pattern=r"^(text|image|binary)$")
    blob: str = Field(max_length=600)


class Completion(Claim):
    files: list[Item] = Field(default_factory=list, max_length=skillfiles.MAX_ENTRIES)
    error: str | None = Field(default=None, pattern=r"^(input_limit|invalid_archive|storage_unavailable)$")


def fenced(db: Session, file_id: str, body: Claim) -> FileObject:
    file = db.get(FileObject, file_id, populate_existing=True)
    job = db.get(OutboxMessage, body.job_id, populate_existing=True)
    view = ((file.meta or {}).get("skill_view") or {}) if file else {}
    if (
        not file
        or not job
        or job.payload.get("kind") != "skill_view"
        or job.payload.get("file_id") != file_id
        or job.claim_token != body.token
        or not job.claimed_until
        or job.claimed_until.replace(tzinfo=UTC) <= datetime.now(UTC)
        or view.get("status") != "preparing"
        or view.get("job_id") != body.job_id
        or file.deleted_at
        or file.purged_at
        or file.status != FileStatus.READY
    ):
        raise HTTPException(409, "stale_claim")
    return file


@router.post("/{file_id}/source")
def source(file_id: str, body: Claim, db: Session = Depends(get_db)):
    file = fenced(db, file_id, body)
    return {"storage_key": file.storage_key, "filename": file.filename, "sha256": file.sha256}


@router.post("/{file_id}/complete")
def complete(file_id: str, body: Completion, db: Session = Depends(get_db)):
    from .derived_assets import publish_writer

    owner = db.get(FileObject, file_id)
    if not owner:
        raise HTTPException(404, "file not found")
    with user_critical_section(db, owner.user_id):
        file = fenced(db, file_id, body)
        items = sorted((item.model_dump() for item in body.files), key=lambda item: item["path"])
        view = failed(body.error) if body.error else _checked(db, file, body, items)
        # Grants of this attempt become readable only together with the manifest that names them; a failure releases them all.
        ready = view["status"] == "ready"
        publish_writer(db, file.id, body.job_id, body.token, failed=not ready, keys=[i["blob"] for i in items] if ready else None)
        file.meta = {**(file.meta or {}), "skill_view": view}
        db.commit()
    return {"ok": ready}


def _checked(db: Session, file: FileObject, body: Completion, items: list[dict]) -> dict:
    """The manifest may only name objects this attempt wrote under this file's own prefix, at their recorded size."""
    prefix = f"derived/{file.id}/skill/"
    blobs = {item["blob"] for item in items}
    assets = {row.key: row for row in db.scalars(select(DerivedAsset).where(DerivedAsset.key.in_(blobs)))} if blobs else {}
    try:
        validate_manifest([{**item, "change": "unchanged"} for item in items], require_skill=True)
    except skillfiles.SkillFileError:
        return failed("invalid_archive")
    for item in items:
        asset = assets.get(item["blob"])
        if (
            item["blob"] != prefix + item["sha256"]
            or not asset
            or asset.state != "staged"
            or asset.job_id != body.job_id
            or asset.attempt_token != body.token
            or asset.size_bytes != item["size"]
        ):
            return failed("invalid_archive")
    return {"status": "ready", "files": items}
