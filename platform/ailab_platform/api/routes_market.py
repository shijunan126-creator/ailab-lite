"""Official skill marketplace API (Phase 1: curated catalog, install as a personal skill)."""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import market
from ..config import settings
from ..deps import current_user, get_db
from ..library import assign_file_version, trim_file_versions
from ..locks import user_critical_section
from ..models import FileObject, FileStatus, User, new_id
from ..quota import enforce_library_quota
from ..storage import get_storage
from .jobs import stage_job
from .routes_skills import personal_skills

router = APIRouter(prefix="/v1/market", tags=["market"])


def _personal_names(db: Session, user: User) -> dict[str, dict]:
    return {item["name"]: item for item in personal_skills(db, user)}


@router.get("/skills")
def market_skills(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """The official catalog, annotated with what this user already has."""
    mine = _personal_names(db, user)
    out = []
    for item in market.listing():
        installed = mine.get(item["name"])
        out.append(
            {
                **{k: v for k, v in item.items() if k != "builtin"},
                "builtin": item["builtin"],
                "installed": bool(installed),
                "installed_file_id": installed["file_id"] if installed else None,
                "installed_error": installed.get("error") if installed else None,
            }
        )
    return out


@router.post("/skills/{slug}/install", status_code=201)
def install_market_skill(slug: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    item = market.find(slug)
    if item is None:
        raise HTTPException(404, "技能不存在")
    if item["builtin"]:
        raise HTTPException(409, "这是内置技能，无需安装")
    try:
        data = market.package_zip(slug)
    except FileNotFoundError as exc:
        raise HTTPException(409, "技能包暂不可用") from exc
    if len(data) > min(settings.max_upload_bytes, settings.max_sandbox_read_bytes):
        raise HTTPException(413, "技能包超过大小上限")
    storage = get_storage()
    with user_critical_section(db, user.id):
        heads = db.scalars(
            select(FileObject).where(
                FileObject.user_id == user.id,
                FileObject.purpose == "skill",
                FileObject.is_head == 1,
                FileObject.deleted_at.is_(None),
                FileObject.purged_at.is_(None),
            )
        ).all()
        if any(
            ((f.meta or {}).get("skill") or {}).get("name") == item["name"] or (f.meta or {}).get("skill_claim") == item["name"]
            for f in heads
        ):
            raise HTTPException(409, "这个技能已经在你的个人技能里")
        enforce_library_quota(db, user.id, len(data))
        file_id = new_id("fil")
        key = f"skills/{user.id}/{file_id}/{slug}.zip"
        storage.put_bytes(key, data, "application/zip")
        try:
            row = FileObject(
                id=file_id,
                user_id=user.id,
                filename=f"{slug}.zip",
                content_type="application/zip",
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
                status=FileStatus.PENDING,
                purpose="skill",
                meta={
                    "processing": "queued",
                    "skill_claim": item["name"],
                    "source": "market",
                    "market_version": item["version"],
                },
            )
            assign_file_version(db, row, None)
            db.add(row)
            keys = trim_file_versions(db, row)
            db.flush()
            stage_job(db, "file", file_id=row.id)
            db.commit()
        except Exception:
            db.rollback()
            storage.delete(key)
            raise
    for stale in keys:
        storage.delete(stale)
    return {
        "file_id": file_id,
        "name": item["name"],
        "title": item["title"],
        "version": item["version"],
        "status": "pending",
    }
