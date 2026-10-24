"""Official + community skill marketplace.

Phase 1 ships a curated catalog in code. Phase 2 lets a user submit a personal skill as an immutable
package snapshot; an administrator reviews that version before it becomes publicly installable.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import market
from ..audit import record_audit
from ..config import settings
from ..deps import current_user, get_db
from ..library import assign_file_version, trim_file_versions
from ..locks import user_critical_section
from ..models import FileObject, FileStatus, MarketListing, MarketReview, MarketVersion, User, new_id, utcnow
from ..quota import enforce_library_quota
from ..storage import get_storage
from .jobs import stage_job
from .routes_skills import _admin, personal_skills

router = APIRouter(prefix="/v1/market", tags=["market"])

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class SubmissionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    category: str = Field(default="其他", max_length=40)
    license: str = Field(min_length=1, max_length=80)
    version: str = Field(default="1.0.0", pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    changelog: str = Field(default="", max_length=2000)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern=r"^(approved|rejected)$")
    reason: str = Field(default="", max_length=1000)


def _personal_names(db: Session, user: User) -> dict[str, dict]:
    return {item["name"]: item for item in personal_skills(db, user)}


def _published_listing(db: Session, slug: str) -> MarketListing | None:
    return db.scalar(select(MarketListing).where(MarketListing.slug == slug, MarketListing.status == "published"))


def _version(db: Session, listing: MarketListing) -> MarketVersion | None:
    if not listing.current_version:
        return None
    return db.scalar(
        select(MarketVersion).where(
            MarketVersion.listing_id == listing.id,
            MarketVersion.version == listing.current_version,
            MarketVersion.status == "approved",
        )
    )


def _community_item(db: Session, listing: MarketListing) -> dict:
    owner = db.get(User, listing.user_id)
    return {
        "slug": listing.slug,
        "name": listing.slug,
        "title": listing.title,
        "description": listing.description,
        "category": listing.category,
        "version": listing.current_version or "",
        "license": listing.license,
        "author": (owner.display_name or owner.username or "社区用户") if owner else "社区用户",
        "builtin": False,
        "requires": [],
        "community": True,
    }


def _snapshot_bytes(source: FileObject) -> bytes:
    data = get_storage().get_bytes(source.storage_key)
    if source.filename.endswith(".md"):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("SKILL.md", data)
        return buffer.getvalue()
    return data


@router.get("/skills")
def market_skills(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    """Curated catalog plus published community skills, annotated for this user."""
    mine = _personal_names(db, user)
    out = []
    for item in market.listing():
        installed = mine.get(item["name"])
        out.append(
            {
                **{k: v for k, v in item.items() if k != "builtin"},
                "builtin": item["builtin"],
                "community": False,
                "installed": bool(installed),
                "installed_file_id": installed["file_id"] if installed else None,
                "installed_error": installed.get("error") if installed else None,
            }
        )
    for listing in db.scalars(
        select(MarketListing).where(MarketListing.status == "published").order_by(MarketListing.published_at.desc(), MarketListing.id)
    ):
        item = _community_item(db, listing)
        installed = mine.get(item["name"])
        out.append(
            {
                **item,
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
        listing = _published_listing(db, slug)
        if listing is None:
            raise HTTPException(404, "技能不存在")
        version = _version(db, listing)
        if version is None:
            raise HTTPException(409, "技能包暂不可用")
        snapshot = db.get(FileObject, version.file_id)
        if snapshot is None or snapshot.deleted_at or snapshot.purged_at:
            raise HTTPException(409, "技能包暂不可用")
        data = get_storage().get_bytes(snapshot.storage_key)
        name = listing.slug
        title = listing.title
        version_text = version.version
    else:
        if item["builtin"]:
            raise HTTPException(409, "这是内置技能，无需安装")
        data = market.package_zip(slug)
        name = item["name"]
        title = item["title"]
        version_text = item["version"]

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
        if any(((f.meta or {}).get("skill") or {}).get("name") == name or (f.meta or {}).get("skill_claim") == name for f in heads):
            raise HTTPException(409, "这个技能已经在你的个人技能里")
        enforce_library_quota(db, user.id, len(data))
        file_id = new_id("fil")
        key = f"skills/{user.id}/{file_id}/{name}.zip"
        storage.put_bytes(key, data, "application/zip")
        try:
            row = FileObject(
                id=file_id,
                user_id=user.id,
                filename=f"{name}.zip",
                content_type="application/zip",
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
                status=FileStatus.PENDING,
                purpose="skill",
                meta={"processing": "queued", "skill_claim": name, "source": "market", "market_version": version_text},
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
    return {"file_id": file_id, "name": name, "title": title, "version": version_text, "status": "pending"}


@router.post("/submissions", status_code=201)
def submit_skill(body: SubmissionInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    source = db.get(FileObject, body.file_id, populate_existing=True)
    if (
        source is None
        or source.user_id != user.id
        or source.purpose != "skill"
        or source.is_head != 1
        or source.deleted_at
        or source.purged_at
        or source.status != FileStatus.READY
    ):
        raise HTTPException(404, "找不到可上架的个人技能")
    skill = (source.meta or {}).get("skill") or {}
    slug = str(skill.get("name") or "")
    if not SLUG_RE.match(slug):
        raise HTTPException(422, "技能名不合规")
    if market.find(slug) or db.scalar(select(MarketListing.id).where(MarketListing.slug == slug)):
        raise HTTPException(409, "市场里已有同名技能")
    data = _snapshot_bytes(source)
    if len(data) > min(settings.max_upload_bytes, settings.max_sandbox_read_bytes):
        raise HTTPException(413, "技能包超过大小上限")
    if not SEMVER_RE.match(body.version):
        raise HTTPException(422, "版本号需要 x.y.z")
    enforce_library_quota(db, user.id, len(data))
    listing_id = new_id("mlst")
    file_id = new_id("fil")
    storage = get_storage()
    key = f"market/{user.id}/{file_id}/{slug}.zip"
    storage.put_bytes(key, data, "application/zip")
    with user_critical_section(db, user.id):
        try:
            snapshot = FileObject(
                id=file_id,
                user_id=user.id,
                filename=f"{slug}.zip",
                content_type="application/zip",
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                storage_key=key,
                status=FileStatus.READY,
                purpose="market",
                meta={"market": True, "skill": skill},
            )
            assign_file_version(db, snapshot, None)
            db.add(snapshot)
            db.flush()
            listing = MarketListing(
                id=listing_id,
                user_id=user.id,
                slug=slug,
                title=body.title,
                description=body.description,
                category=body.category,
                license=body.license,
                status="pending",
            )
            db.add(listing)
            db.flush()
            version = MarketVersion(
                id=new_id("mver"),
                listing_id=listing.id,
                version=body.version,
                file_id=snapshot.id,
                changelog=body.changelog,
                status="pending",
            )
            db.add(version)
            record_audit(db, "market.submitted", "market_listing", listing.id, actor_id=user.id, detail={"fields": ["status"]})
            db.commit()
        except Exception:
            db.rollback()
            storage.delete(key)
            raise
    return {"listing_id": listing_id, "slug": slug, "version": body.version, "status": "pending"}


@router.get("/submissions/mine")
def my_submissions(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    out = []
    for listing in db.scalars(select(MarketListing).where(MarketListing.user_id == user.id).order_by(MarketListing.created_at.desc())):
        latest = db.scalar(select(MarketVersion).where(MarketVersion.listing_id == listing.id).order_by(MarketVersion.created_at.desc()))
        out.append(
            {
                "listing_id": listing.id,
                "slug": listing.slug,
                "title": listing.title,
                "status": listing.status,
                "version": latest.version if latest else None,
                "version_status": latest.status if latest else None,
                "reason": latest.review_reason if latest else "",
                "created_at": listing.created_at,
            }
        )
    return out


@router.post("/submissions/{listing_id}/withdraw")
def withdraw_submission(listing_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    listing = db.get(MarketListing, listing_id)
    if listing is None or listing.user_id != user.id:
        raise HTTPException(404, "提交不存在")
    if listing.status not in ("pending", "rejected"):
        raise HTTPException(409, "只有待审或被拒绝的提交可以撤回")
    listing.status = "withdrawn"
    listing.updated_at = utcnow()
    for version in db.scalars(select(MarketVersion).where(MarketVersion.listing_id == listing.id, MarketVersion.status == "pending")):
        version.status = "withdrawn"
    record_audit(db, "market.withdrawn", "market_listing", listing.id, actor_id=user.id, detail={"fields": ["status"]})
    db.commit()
    return {"listing_id": listing.id, "status": "withdrawn"}


@router.get("/admin/submissions")
def admin_submissions(status: str = "pending", user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    _admin(user)
    if status not in ("pending", "approved", "rejected"):
        raise HTTPException(422, "invalid status")
    out = []
    for listing in db.scalars(
        select(MarketListing).where(MarketListing.status == status).order_by(MarketListing.created_at, MarketListing.id)
    ):
        latest = db.scalar(select(MarketVersion).where(MarketVersion.listing_id == listing.id).order_by(MarketVersion.created_at.desc()))
        out.append(
            {
                "listing_id": listing.id,
                "slug": listing.slug,
                "title": listing.title,
                "owner_id": listing.user_id,
                "version": latest.version if latest else None,
                "version_status": latest.status if latest else None,
                "created_at": listing.created_at,
            }
        )
    return out


@router.get("/admin/submissions/{listing_id}")
def admin_submission(listing_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    _admin(user)
    listing = db.get(MarketListing, listing_id)
    if listing is None:
        raise HTTPException(404, "提交不存在")
    version = db.scalar(select(MarketVersion).where(MarketVersion.listing_id == listing.id).order_by(MarketVersion.created_at.desc()))
    snapshot = db.get(FileObject, version.file_id) if version else None
    names, skill_md = [], ""
    if snapshot:
        data = get_storage().get_bytes(snapshot.storage_key)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = sorted(info.filename for info in archive.infolist() if not info.is_dir())
                if "SKILL.md" in names:
                    skill_md = archive.read("SKILL.md").decode("utf-8", "replace")[:20000]
        except zipfile.BadZipFile:
            names = []
    return {
        "listing": {
            "id": listing.id,
            "slug": listing.slug,
            "title": listing.title,
            "description": listing.description,
            "category": listing.category,
            "license": listing.license,
            "owner_id": listing.user_id,
            "status": listing.status,
        },
        "version": {
            "id": version.id,
            "version": version.version,
            "status": version.status,
            "changelog": version.changelog,
            "reason": version.review_reason,
        }
        if version
        else None,
        "files": names,
        "skill_md": skill_md,
    }


@router.post("/admin/submissions/{listing_id}/review")
def review_submission(listing_id: str, body: ReviewInput, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    _admin(user)
    listing = db.get(MarketListing, listing_id)
    if listing is None:
        raise HTTPException(404, "提交不存在")
    if listing.user_id == user.id:
        raise HTTPException(409, "不能审核自己的提交")
    version = db.scalar(select(MarketVersion).where(MarketVersion.listing_id == listing.id, MarketVersion.status == "pending"))
    if version is None:
        raise HTTPException(409, "没有待审版本")
    version.status = body.decision
    version.review_reason = body.reason
    version.reviewed_by = user.id
    version.reviewed_at = utcnow()
    db.add(MarketReview(id=new_id("mrev"), version_id=version.id, reviewer_id=user.id, decision=body.decision, reason=body.reason))
    if body.decision == "approved":
        listing.status = "published"
        listing.current_version = version.version
        listing.published_at = utcnow()
    else:
        listing.status = "rejected"
    listing.updated_at = utcnow()
    record_audit(db, f"market.{body.decision}", "market_listing", listing.id, actor_id=user.id, detail={"fields": ["status"]})
    db.commit()
    return {"listing_id": listing.id, "status": listing.status, "version": version.version, "decision": body.decision}
