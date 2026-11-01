"""Administrator publication and user subscriptions; bytes always pass through the Worker."""

import hashlib
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..audit import record_audit
from ..config import settings
from ..deps import current_user, get_db
from ..library import assign_file_version, delete_storage_objects, purge_lineages
from ..locks import user_critical_section
from ..models import FileObject, FileStatus, PlatformSkill, SkillDraft, User, UserPlatformSkill, new_id, utcnow
from ..skillfiles import MAX_SKILL_MD, MAX_ZIP
from ..storage import get_storage
from .brand import brand_lock
from .jobs import stage_job
from .platform_skills import SYSTEM_OWNER, active_skill, added_skills, assert_available_name, subscription
from .routes_skills import _admin

router = APIRouter(tags=["platform-skills"])


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10000)
    category: str = Field(default="", max_length=120)
    source_url: str = Field(default="", max_length=2048)
    license: str = Field(default="", max_length=120)


class SkillPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=10000)
    category: str | None = Field(default=None, max_length=120)
    source_url: str | None = Field(default=None, max_length=2048)
    license: str | None = Field(default=None, max_length=120)


def output(row, sub=None):
    return {
        **{
            k: getattr(row, k)
            for k in (
                "id",
                "name",
                "title",
                "description",
                "category",
                "source_url",
                "license",
                "current_file_id",
                "created_at",
                "updated_at",
            )
        },
        "kind": "platform",
        "added": bool(sub),
        "customized": bool(sub and sub.custom_lineage_id),
        "update_available": bool(sub and sub.custom_lineage_id and sub.base_file_id != row.current_file_id),
    }


@router.get("/v1/platform-skills")
def listing(
    after: str = Query(default="", max_length=40),
    limit: int = Query(default=100, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(PlatformSkill, UserPlatformSkill)
        .outerjoin(UserPlatformSkill, (UserPlatformSkill.skill_id == PlatformSkill.id) & (UserPlatformSkill.user_id == user.id))
        .where(PlatformSkill.deleted_at.is_(None), PlatformSkill.current_file_id.is_not(None), PlatformSkill.id > after)
        .order_by(PlatformSkill.id)
        .limit(limit + 1)
    ).all()
    return {"items": [output(s, sub) for s, sub in rows[:limit]], "next_cursor": rows[limit - 1][0].id if len(rows) > limit else None}


@router.get("/v1/admin/platform-skills")
def admin_listing(
    after: str = Query(default="", max_length=40),
    limit: int = Query(default=100, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _admin(user)
    rows = db.scalars(
        select(PlatformSkill)
        .where(PlatformSkill.deleted_at.is_(None), PlatformSkill.id > after)
        .order_by(PlatformSkill.id)
        .limit(limit + 1)
    ).all()
    return {"items": [output(s) for s in rows[:limit]], "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.post("/v1/admin/platform-skills", status_code=201)
def create(body: SkillCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    with brand_lock(db):
        assert_available_name(db, body.name)
        # Deleted names remain reserved: detached personal copies and historical policies retain this identity.
        if db.scalar(select(PlatformSkill.id).where(PlatformSkill.name == body.name)):
            raise HTTPException(422, "技能名称已使用")
        row = PlatformSkill(**body.model_dump(), created_by=user.id)
        db.add(row)
        db.flush()
        record_audit(db, "platform_skills.create", "skill", row.id, actor_id=user.id)
        db.commit()
        return output(row)


@router.patch("/v1/admin/platform-skills/{skill_id}")
def patch(skill_id: str, body: SkillPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    with brand_lock(db):
        row = active_skill(db, skill_id)
        for key, value in body.model_dump(exclude_unset=True).items():
            if value is None:
                raise HTTPException(422, "字段不能为 null")
            setattr(row, key, value)
        record_audit(db, "platform_skills.update", "skill", row.id, actor_id=user.id)
        db.commit()
        return output(row)


@router.post("/v1/admin/platform-skills/{skill_id}/versions", status_code=202)
async def upload_version(skill_id: str, upload: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in {".md", ".zip"}:
        raise HTTPException(422, "请上传 Markdown 或 ZIP 技能")
    limit = min(settings.max_upload_bytes, settings.max_sandbox_read_bytes, MAX_ZIP if suffix == ".zip" else MAX_SKILL_MD)
    with tempfile.NamedTemporaryFile() as tmp:
        size, digest = 0, hashlib.sha256()
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, "技能包超过大小限制")
            digest.update(chunk)
            await run_in_threadpool(tmp.write, chunk)
        await run_in_threadpool(tmp.flush)
        if not size:
            raise HTTPException(422, "文件为空")

        def save():
            with brand_lock(db):
                row = active_skill(db, skill_id)
                if not db.get(User, SYSTEM_OWNER):
                    db.add(User(id=SYSTEM_OWNER, email="platform-skills@system.invalid", display_name="平台技能", disabled_at=utcnow()))
                    db.flush()
                parent = db.scalar(
                    select(FileObject)
                    .where(
                        FileObject.user_id == SYSTEM_OWNER,
                        FileObject.meta["platform_skill_id"].as_string() == row.id,
                        FileObject.is_head == 1,
                    )
                    .limit(1)
                )
                fid = new_id("fil")
                filename = row.name + suffix
                key = f"skills/{SYSTEM_OWNER}/{fid}/{filename}"
                storage = get_storage()
                storage.put_file(key, tmp.name)
                try:
                    file = FileObject(
                        id=fid,
                        user_id=SYSTEM_OWNER,
                        filename=filename,
                        content_type="application/zip" if suffix == ".zip" else "text/markdown",
                        size=size,
                        sha256=digest.hexdigest(),
                        storage_key=key,
                        status=FileStatus.PENDING,
                        purpose="platform_skill",
                        meta={"processing": "queued", "skill_claim": row.name, "platform_skill_id": row.id},
                    )
                    assign_file_version(db, file, parent)
                    db.add(file)
                    db.flush()
                    stage_job(db, "file", file_id=fid)
                    record_audit(db, "platform_skills.version", "skill", row.id, actor_id=user.id, detail={"file_id": fid})
                    db.commit()
                    return {"file_id": fid, "status": "pending", "version": file.version}
                except Exception:
                    db.rollback()
                    storage.delete(key)
                    raise

        return await run_in_threadpool(save)


@router.post("/v1/admin/platform-skills/{skill_id}/publish/{file_id}")
def publish(skill_id: str, file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    with brand_lock(db):
        row = active_skill(db, skill_id)
        file = db.get(FileObject, file_id, populate_existing=True)
        if (
            not file
            or file.purpose != "platform_skill"
            or file.user_id != SYSTEM_OWNER
            or file.deleted_at
            or file.purged_at
            or (
                file.status != FileStatus.READY
                or (file.meta or {}).get("platform_skill_id") != row.id
                or not (file.meta or {}).get("skill")
                or (file.meta or {}).get("skill_error")
            )
        ):
            raise HTTPException(422, "版本未通过扫描或不属于此技能")
        row.current_file_id = file.id
        file.meta = {**(file.meta or {}), "platform_published": True}
        record_audit(db, "platform_skills.publish", "skill", row.id, actor_id=user.id, detail={"file_id": file_id})
        db.commit()
        return output(row)


@router.post("/v1/platform-skills/{skill_id}/add")
def add(skill_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with brand_lock(db), user_critical_section(db, user.id):
        row = active_skill(db, skill_id)
        if not row.current_file_id:
            raise HTTPException(404, "技能尚未发布")
        sub = db.get(UserPlatformSkill, (user.id, skill_id))
        if not sub:
            if db.scalar(select(func.count()).select_from(UserPlatformSkill).where(UserPlatformSkill.user_id == user.id)) >= 100:
                raise HTTPException(422, "最多添加 100 个平台技能")
            sub = UserPlatformSkill(user_id=user.id, skill_id=skill_id)
            db.add(sub)
        db.commit()
        return output(row, sub)


def clear_copy(db, user, sub):
    lineage = sub.custom_lineage_id
    sub.custom_lineage_id, sub.base_file_id = None, None
    db.flush()
    keys = purge_lineages(db, user.id, [lineage]) if lineage else []
    # An already-open customize draft must not resurrect a reset or removed subscription.
    for draft in db.scalars(
        select(SkillDraft).where(
            SkillDraft.user_id == user.id,
            SkillDraft.origin == "platform",
            SkillDraft.target == sub.skill_id,
            SkillDraft.status.in_(("preparing", "ready", "failed")),
        )
    ):
        from ..library import schedule_object_deletion

        schedule_object_deletion(db, f"skill-drafts/{user.id}/{draft.id}/", "skill_draft", prefix=True)
        draft.status, draft.files, draft.claim_token = "discarded", [], None
    return keys


@router.post("/v1/platform-skills/{skill_id}/reset")
def reset(skill_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with brand_lock(db), user_critical_section(db, user.id):
        row = active_skill(db, skill_id)
        sub = subscription(db, user.id, skill_id)
        keys = clear_copy(db, user, sub)
        db.commit()
        result = output(row, sub)
    delete_storage_objects(keys)
    return result


@router.delete("/v1/platform-skills/{skill_id}/add", status_code=204)
def remove(skill_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with brand_lock(db), user_critical_section(db, user.id):
        sub = db.get(UserPlatformSkill, (user.id, skill_id))
        keys = clear_copy(db, user, sub) if sub else []
        if sub:
            db.delete(sub)
        db.commit()
    delete_storage_objects(keys)


@router.delete("/v1/admin/platform-skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    with brand_lock(db):
        row = active_skill(db, skill_id)
        row.deleted_at = utcnow()
        # Copies already are personal files: deleting the association detaches without copying or quota changes.
        db.execute(delete(UserPlatformSkill).where(UserPlatformSkill.skill_id == row.id))
        record_audit(db, "platform_skills.delete", "skill", row.id, actor_id=user.id)
        db.commit()


@router.post("/v1/platform-skills/{skill_id}/customize", status_code=202)
def customize(skill_id: str, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return open_platform_draft(skill_id, response, user, db)


@router.post("/v1/admin/platform-skills/{skill_id}/drafts", status_code=202)
def admin_draft(skill_id: str, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _admin(user)
    return open_platform_draft(skill_id, response, user, db, admin=True)


def open_platform_draft(skill_id, response, user, db, *, admin=False):
    from ..skill_drafts.files import store_file, update_metadata
    from .routes_skill_drafts import ACTIVE, _output, _room

    with brand_lock(db), user_critical_section(db, user.id):
        skill = active_skill(db, skill_id)
        sub = None if admin else subscription(db, user.id, skill_id)
        if admin:
            file = db.get(FileObject, skill.current_file_id) if skill.current_file_id else None
        else:
            base = next((s for s in added_skills(db, user) if s["id"] == skill.id), None)
            file = db.get(FileObject, base["file_id"]) if base and not base["error"] else None
        if not file or file.status != FileStatus.READY:
            raise HTTPException(422, "技能不可用")
        origin = "platform_admin" if admin else "platform"
        existing = db.scalar(
            select(SkillDraft)
            .where(
                SkillDraft.user_id == user.id,
                SkillDraft.origin == origin,
                SkillDraft.target == skill_id,
                SkillDraft.status.in_(ACTIVE),
                SkillDraft.base_file_id == file.id,
            )
            .limit(1)
        )
        if existing:
            response.status_code = 200
            return _output(db, existing)
        _room(db, user.id)
        row = SkillDraft(
            id=new_id("sd"),
            user_id=user.id,
            origin=origin,
            target=skill.id,
            base_file_id=file.id,
            base_kind="platform",
            base_name=skill.name,
            action="update" if admin or sub.custom_lineage_id else "create",
            status="preparing",
            stage="unpacking",
            files=[],
        )
        if admin:
            head = db.scalar(select(FileObject).where(FileObject.lineage_id == file.lineage_id, FileObject.is_head == 1))
            row.result = {"expected_head_file_id": head.id if head else None}
        db.add(row)
        if file.filename.lower().endswith(".zip"):
            row.claim_token = stage_job(db, "skill_unpack", draft_id=row.id).id
        else:
            item = store_file(get_storage(), user.id, row.id, "SKILL.md", get_storage().get_bytes(file.storage_key), "unchanged")
            item["baseline"] = item["sha256"]
            row.files, row.status, row.stage = [item], "ready", None
            update_metadata(row, get_storage())
        db.commit()
        return _output(db, row)


@router.get("/v1/admin/platform-skills/{skill_id}/versions")
def versions(
    skill_id: str,
    before: int = Query(default=2147483647, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    _admin(user)
    row = active_skill(db, skill_id)
    head = db.scalar(
        select(FileObject)
        .where(
            FileObject.user_id == SYSTEM_OWNER,
            FileObject.purpose == "platform_skill",
            FileObject.meta["platform_skill_id"].as_string() == row.id,
            FileObject.is_head == 1,
        )
        .limit(1)
    )
    files = (
        db.scalars(
            select(FileObject)
            .where(FileObject.lineage_id == head.lineage_id, FileObject.version < before, FileObject.purged_at.is_(None))
            .order_by(FileObject.version.desc())
            .limit(limit + 1)
        ).all()
        if head
        else []
    )
    return {
        "items": [
            {
                "file_id": f.id,
                "filename": f.filename,
                "version": f.version,
                "status": f.status,
                "error": (f.meta or {}).get("skill_error"),
                "current": f.id == row.current_file_id,
                "published": bool((f.meta or {}).get("platform_published")),
                "created_at": f.created_at,
            }
            for f in files[:limit]
        ],
        "next_cursor": files[limit - 1].version if len(files) > limit else None,
    }


@router.get("/v1/admin/platform-skills/{skill_id}/versions/{file_id}/content")
def version_content(skill_id: str, file_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from fastapi.responses import StreamingResponse

    _admin(user)
    active_skill(db, skill_id)
    file = db.get(FileObject, file_id)
    if (
        not file
        or file.user_id != SYSTEM_OWNER
        or file.purpose != "platform_skill"
        or file.deleted_at
        or file.purged_at
        or (
            (file.meta or {}).get("platform_skill_id") != skill_id
            or file.status != FileStatus.READY
            or (file.meta or {}).get("skill_error")
        )
    ):
        raise HTTPException(404, "版本不可用")
    return StreamingResponse(
        get_storage().stream(file.storage_key),
        media_type="application/octet-stream",
        headers={"X-Content-Type-Options": "nosniff", "Content-Disposition": f'attachment; filename="{file.filename}"'},
    )
