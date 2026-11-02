"""Private skill draft editing and confirmation."""

from __future__ import annotations

import hashlib
import tempfile
import zipfile
from contextlib import nullcontext
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .. import skillfiles
from ..config import settings
from ..deps import current_user, get_db
from ..library import assign_file_version, delete_storage_objects, schedule_object_deletion, trim_file_versions
from ..locks import user_critical_section
from ..models import Conversation, FileObject, FileStatus, SkillDraft, User, new_id, utcnow
from ..quota import enforce_library_quota
from ..skill_drafts.files import MAX_TEXT, SAFE_IMAGES, checked_path, store_file, update_metadata, validate_manifest
from ..storage import get_storage
from .jobs import stage_job
from .routes_internal import _skill_heads
from .routes_skills import personal_skills

router = APIRouter(tags=["skill-drafts"])
ACTIVE = ("queued", "running", "preparing", "ready", "failed")


class ExtractIn(BaseModel):
    target: str | None = Field(default=None, max_length=120)


class RevisionIn(BaseModel):
    expected_revision: int = Field(ge=0)


class MoveIn(RevisionIn):
    source: str = Field(alias="from", max_length=1024)
    to: str = Field(max_length=1024)


def _owned(db, user, draft_id):
    row = db.get(SkillDraft, draft_id, populate_existing=True)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "草稿不存在")
    if row.origin == "platform_admin":
        from .routes_skills import _admin

        _admin(user)
    return row


def _editable(row, revision):
    if row.status != "ready":
        raise HTTPException(409, "草稿尚不可编辑")
    if row.revision != revision:
        raise HTTPException(409, "草稿已被修改，请刷新后重试")


def _check(files, require_skill=False):
    try:
        validate_manifest(files, require_skill=require_skill)
    except skillfiles.SkillFileError as exc:
        raise HTTPException(422, str(exc)) from exc


def _path(path):
    try:
        return checked_path(path)
    except skillfiles.SkillFileError as exc:
        raise HTTPException(422, str(exc)) from exc


def _output(db, row, include_files=True):
    result = {
        key: getattr(row, key)
        for key in (
            "id",
            "status",
            "stage",
            "origin",
            "conversation_id",
            "action",
            "summary",
            "error",
            "revision",
            "created_at",
            "updated_at",
            "result",
        )
    }
    if row.origin == "platform_admin" and row.status != "confirmed":
        result["result"] = None
    conv = db.get(Conversation, row.conversation_id) if row.conversation_id else None
    result["conversation_title"] = conv.title if conv and not conv.deleted_at else None
    result["base"] = {"name": row.base_name, "kind": row.base_kind, "file_id": row.base_file_id} if row.base_name else None
    if include_files:
        result["files"] = [{k: f[k] for k in ("path", "size", "kind", "change")} for f in row.files or []]
    result.update({k: getattr(row, k) for k in ("name", "title", "description")})
    return result


def _room(db, user_id):
    count = db.scalar(select(func.count()).select_from(SkillDraft).where(SkillDraft.user_id == user_id, SkillDraft.status.in_(ACTIVE)))
    if count >= 20:
        raise HTTPException(409, "活动草稿已达上限")


@router.post("/v1/conversations/{conversation_id}/skill-drafts", status_code=202)
def extract(conversation_id: str, body: ExtractIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        conv = db.get(Conversation, conversation_id, populate_existing=True)
        if not conv or conv.user_id != user.id or conv.deleted_at:
            raise HTTPException(404, "对话不存在")
        _room(db, user.id)
        row = SkillDraft(
            id=new_id("sd"),
            user_id=user.id,
            origin="conversation",
            conversation_id=conv.id,
            action="create",
            status="queued",
            stage="reading",
            target=body.target,
            files=[],
        )
        db.add(row)
        db.commit()
        return _output(db, row)


@router.get("/v1/skill-drafts")
def listing(active: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(SkillDraft).where(SkillDraft.user_id == user.id)
    if active:
        query = query.where(SkillDraft.status.in_(ACTIVE))
    return [_output(db, row, False) for row in db.scalars(query.order_by(SkillDraft.updated_at.desc(), SkillDraft.id.desc()).limit(50))]


@router.get("/v1/skill-drafts/{draft_id}")
def detail(draft_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _output(db, _owned(db, user, draft_id))


@router.get("/v1/skill-drafts/{draft_id}/files/{path:path}")
def read_file(draft_id: str, path: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _path(path)
    row = _owned(db, user, draft_id)
    item = next((f for f in row.files or [] if f["path"] == path and f["change"] != "deleted"), None)
    if not item or row.status not in ("ready", "confirmed"):
        raise HTTPException(404, "文件不存在")
    suffix = PurePosixPath(path).suffix.lower()
    image = suffix in SAFE_IMAGES and item["kind"] == "image"
    text_file = item["kind"] == "text" and suffix not in {".svg", ".html", ".htm"}
    content_type = SAFE_IMAGES[suffix] if image else "text/plain; charset=utf-8" if text_file else "application/octet-stream"
    headers = {"X-Content-Type-Options": "nosniff", "Content-Disposition": "inline" if image or text_file else "attachment"}
    return StreamingResponse(get_storage().stream(item["blob"]), media_type=content_type, headers=headers)


@router.put("/v1/skill-drafts/{draft_id}/files/{path:path}")
async def write_file(draft_id: str, path: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _path(path)
    if request.headers.get("content-type", "").startswith("application/json"):
        body = await request.json()
        if not isinstance(body.get("content"), str) or not isinstance(body.get("expected_revision"), int):
            raise HTTPException(422, "内容或版本无效")
        data = body["content"].encode()
        revision = body["expected_revision"]
        if len(data) > MAX_TEXT:
            raise HTTPException(413, "文本文件超过 512 KB")
    else:
        form = await request.form()
        upload = form.get("file")
        try:
            revision = int(form.get("expected_revision"))
            data = await upload.read(skillfiles.MAX_UNPACKED + 1)
        except (ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(422, "文件或版本无效") from exc

    def save():
        with user_critical_section(db, user.id):
            row = _owned(db, user, draft_id)
            _editable(row, revision)
            old = next((f for f in row.files if f["path"] == path), None)
            if len(data) > skillfiles.MAX_UNPACKED:
                raise HTTPException(413, "文件超过大小限制")
            item = store_file(get_storage(), user.id, row.id, path, data, "added")
            if old:
                baseline = old.get("baseline")
                item["baseline"] = baseline
                item["change"] = "unchanged" if baseline == item["sha256"] else "modified" if baseline else "added"
            files = [f for f in row.files if f["path"] != path] + [item]
            _check(files)
            row.files = files
            update_metadata(row, get_storage())
            row.revision += 1
            row.updated_at = utcnow()
            db.commit()
            return _output(db, row)

    # The user lock and the database are blocking: waiting for either on the event loop would stall every request.
    return await run_in_threadpool(save)


@router.post("/v1/skill-drafts/{draft_id}/move")
def move(draft_id: str, body: MoveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    source, target = _path(body.source), _path(body.to)
    if target.startswith(source + "/"):
        raise HTTPException(422, "不能移动到自身目录中")
    with user_critical_section(db, user.id):
        row = _owned(db, user, draft_id)
        _editable(row, body.expected_revision)
        selected = [f for f in row.files if f["change"] != "deleted" and (f["path"] == source or f["path"].startswith(source + "/"))]
        if not selected:
            raise HTTPException(404, "文件不存在")
        if source == target:
            return _output(db, row)
        files = [f for f in row.files if f not in selected]
        for item in selected:
            if item.get("baseline"):
                files.append({**item, "change": "deleted"})
            new_path = target + item["path"][len(source) :]
            existing = next((f for f in files if f["path"] == new_path), None)
            if existing and existing["change"] != "deleted":
                raise HTTPException(409, "目标文件已存在")
            baseline = existing.get("baseline") if existing else None
            files = [f for f in files if f["path"] != new_path]
            change = "unchanged" if baseline == item["sha256"] else "modified" if baseline else "added"
            files.append({**item, "path": new_path, "change": change, "baseline": baseline})
        _check(files)
        row.files = files
        update_metadata(row, get_storage())
        row.revision += 1
        row.updated_at = utcnow()
        db.commit()
        return _output(db, row)


@router.delete("/v1/skill-drafts/{draft_id}/files/{path:path}")
def delete_file(draft_id: str, path: str, expected_revision: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _path(path)
    with user_critical_section(db, user.id):
        row = _owned(db, user, draft_id)
        _editable(row, expected_revision)
        selected = [f for f in row.files if f["change"] != "deleted" and (f["path"] == path or f["path"].startswith(path + "/"))]
        if not selected:
            raise HTTPException(404, "文件不存在")
        row.files = [f for f in row.files if f not in selected] + [{**f, "change": "deleted"} for f in selected if f.get("baseline")]
        update_metadata(row, get_storage())
        row.revision += 1
        row.updated_at = utcnow()
        db.commit()
        return _output(db, row)


@router.delete("/v1/skill-drafts/{draft_id}")
def discard(draft_id: str, expected_revision: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        row = _owned(db, user, draft_id)
        if row.status not in ACTIVE or row.revision != expected_revision:
            raise HTTPException(409, "草稿状态或版本已改变，请刷新后重试")
        row.status, row.stage, row.claim_token, row.files = "discarded", None, None, []
        row.name, row.title, row.description = None, None, None
        row.revision += 1
        prefix = f"skill-drafts/{user.id}/{row.id}/"
        schedule_object_deletion(db, prefix, "skill_draft", prefix=True)
        db.commit()
    delete_storage_objects([prefix])
    return {"status": "discarded"}


@router.post("/v1/skill-drafts/{draft_id}/confirm")
def confirm(draft_id: str, body: RevisionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    storage = get_storage()
    from .brand import brand_lock

    draft = _owned(db, user, draft_id)
    with brand_lock(db) if draft.origin in {"platform", "platform_admin"} else nullcontext(), user_critical_section(db, user.id):
        row = _owned(db, user, draft_id)
        if row.status == "confirmed" and row.result:
            return row.result
        _editable(row, body.expected_revision)
        files = [f for f in row.files if f["change"] != "deleted"]
        _check(files, True)
        md = next(f for f in files if f["path"] == "SKILL.md")
        try:
            info = skillfiles.inspect(storage.get_bytes(md["blob"]), "SKILL.md")
            if not info:
                raise skillfiles.SkillFileError("SKILL.md 缺少有效的技能信息")
        except skillfiles.SkillFileError as exc:
            raise HTTPException(422, str(exc)) from exc
        name = info["name"]
        from .platform_skills import SYSTEM_OWNER, active_skill, check_name, subscription

        platform_sub = None
        platform_admin = row.origin == "platform_admin"
        if platform_admin:
            platform_skill = active_skill(db, row.target)
            if name != platform_skill.name:
                raise HTTPException(422, "平台技能不能更改名称")
        elif row.origin == "platform":
            platform_skill = active_skill(db, row.target)
            platform_sub = subscription(db, user.id, platform_skill.id)
            if name != platform_skill.name:
                raise HTTPException(422, "自定义技能不能更改名称")
            if row.action == "create" and platform_sub.custom_lineage_id:
                raise HTTPException(409, "技能已自定义，请重新打开")
        else:
            check_name(db, name)
        from ..agent.prompts import skills

        if name in skills():
            raise HTTPException(409, "不能覆盖平台技能")
        heads = _skill_heads(db, user.id, name)
        parent = db.get(FileObject, row.base_file_id, populate_existing=True) if row.action == "update" else None
        if row.action == "update":
            if (
                not parent
                or (not parent.is_head and not platform_admin)
                or parent.deleted_at
                or parent.purged_at
                or parent.status != FileStatus.READY
                or (heads and any(h.id != parent.id for h in heads))
            ):
                raise HTTPException(409, "技能已被修改，请重新打开最新版本")
        elif heads:
            raise HTTPException(409, "已有同名个人技能")
        if platform_admin:
            head = db.scalar(select(FileObject).where(FileObject.lineage_id == parent.lineage_id, FileObject.is_head == 1))
            if not head or head.id != (row.result or {}).get("expected_head_file_id") or platform_skill.current_file_id != row.base_file_id:
                raise HTTPException(409, "平台版本已改变，请重新打开草稿")
        is_zip = len(files) != 1 or bool(parent and parent.filename.lower().endswith(".zip"))
        with tempfile.NamedTemporaryFile() as tmp:
            if is_zip:
                with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for item in sorted(files, key=lambda f: f["path"]):
                        entry = zipfile.ZipInfo(item["path"], (1980, 1, 1, 0, 0, 0))
                        entry.compress_type = zipfile.ZIP_DEFLATED
                        entry.external_attr = 0o100644 << 16
                        with archive.open(entry, "w") as dst:
                            for chunk in storage.stream(item["blob"]):
                                dst.write(chunk)
            else:
                for chunk in storage.stream(md["blob"]):
                    tmp.write(chunk)
            tmp.flush()
            size = tmp.tell()
            if size > min(
                settings.max_upload_bytes, settings.max_sandbox_read_bytes, skillfiles.MAX_ZIP if is_zip else skillfiles.MAX_SKILL_MD
            ):
                raise HTTPException(413, "技能包超过大小限制")
            if not platform_admin:
                enforce_library_quota(db, user.id, size)
            tmp.seek(0)
            digest = hashlib.file_digest(tmp, "sha256").hexdigest()
            file_id = new_id("fil")
            filename = name + (".zip" if is_zip else ".md")
            owner_id = SYSTEM_OWNER if platform_admin else user.id
            key = f"skills/{owner_id}/{file_id}/{filename}"
            content_type = "application/zip" if is_zip else "text/markdown"
            storage.put_file(key, tmp.name, content_type)
            try:
                file = FileObject(
                    id=file_id,
                    user_id=owner_id,
                    filename=filename,
                    content_type=content_type,
                    size=size,
                    sha256=digest,
                    storage_key=key,
                    status=FileStatus.PENDING,
                    purpose="platform_skill" if platform_admin else "skill",
                    meta={
                        "processing": "queued",
                        "skill_claim": name,
                        "source": "draft",
                        **({"platform_skill_id": platform_skill.id} if platform_admin else {}),
                    },
                )
                assign_file_version(db, file, parent)
                db.add(file)
                stale = trim_file_versions(db, file)
                db.flush()
                stage_job(db, "file", file_id=file.id)
                if platform_sub is not None:
                    platform_sub.custom_lineage_id = file.lineage_id
                    platform_sub.base_file_id = platform_sub.base_file_id or row.base_file_id
                if platform_admin:
                    from ..audit import record_audit

                    record_audit(db, "platform_skills.version", "skill", platform_skill.id, actor_id=user.id, detail={"file_id": file.id})
                row.result = {"file_id": file.id, "name": name, "version": file.version}
                row.status, row.stage = "confirmed", None
                db.commit()
            except Exception:
                db.rollback()
                storage.delete(key)
                raise
    delete_storage_objects(stale)
    return row.result


@router.post("/v1/skills/{name}/drafts", status_code=202)
def open_skill(name: str, response: Response, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..skill_index import checked_index
    from .platform_skills import platform_name
    from .routes_platform_skills import customize

    platform = platform_name(db, name)
    if platform:
        return customize(platform.id, response, user, db)
    storage = get_storage()
    with user_critical_section(db, user.id):
        personal = next((s for s in personal_skills(db, user) if s["name"] == name and not s.get("error")), None)
        base_file_id = personal["file_id"] if personal else None
        existing = db.scalar(
            select(SkillDraft)
            .where(
                SkillDraft.user_id == user.id,
                SkillDraft.origin == "skill",
                SkillDraft.status.in_(ACTIVE),
                SkillDraft.base_file_id == base_file_id
                if personal
                else (SkillDraft.base_file_id.is_(None) & (SkillDraft.base_name == name)),
            )
            .order_by(SkillDraft.created_at.desc())
            .limit(1)
        )
        if existing:
            response.status_code = 200
            return _output(db, existing)
        _room(db, user.id)
        row = SkillDraft(
            id=new_id("sd"),
            user_id=user.id,
            origin="skill",
            action="update" if personal else "create",
            status="preparing",
            stage="unpacking",
            base_name=name,
            base_kind="personal" if personal else "base" if name.startswith("platform.") else "builtin",
            files=[],
        )
        if personal:
            file = db.get(FileObject, personal["file_id"], populate_existing=True)
            if not file or file.status != FileStatus.READY or not file.is_head or file.deleted_at or file.purged_at:
                raise HTTPException(409, "技能不可用")
            row.base_file_id = file.id
            if file.filename.lower().endswith(".zip"):
                db.add(row)
                row.claim_token = stage_job(db, "skill_unpack", draft_id=row.id).id
            else:
                data = storage.get_bytes(file.storage_key)
                item = store_file(storage, user.id, row.id, "SKILL.md", data, "unchanged")
                item["baseline"] = item["sha256"]
                row.files, row.status, row.stage = [item], "ready", None
                db.add(row)
        else:
            index = checked_index(settings.skills_path)["skills"]
            if name not in index:
                raise HTTPException(404, "技能不存在")
            directory = settings.skills_path / name
            custom_name = name.removeprefix("platform.")[:33].rstrip("-") + "-custom"
            entries = []
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    relative = path.relative_to(directory).as_posix()
                    data = path.read_bytes()
                    if relative == "SKILL.md":
                        import re

                        data = re.sub(rb"(?m)^name:\s*[^\r\n]*", b"name: " + custom_name.encode(), data, count=1)
                    entries.append(store_file(storage, user.id, row.id, relative, data, "added"))
            _check(entries, True)
            row.files, row.status, row.stage = entries, "ready", None
            db.add(row)
        update_metadata(row, storage)
        db.commit()
        return _output(db, row)


@router.post("/v1/skill-drafts/{draft_id}/retry")
def retry(draft_id: str, expected_revision: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    with user_critical_section(db, user.id):
        row = _owned(db, user, draft_id)
        if row.status != "failed" or row.revision != expected_revision:
            raise HTTPException(409, "草稿状态或版本已改变，请刷新后重试")
        row.revision += 1
        row.error, row.claim_token, row.claimed_until = None, None, None
        if row.base_file_id and (row.origin == "skill" or not row.files):
            row.status, row.stage = "preparing", "unpacking"
            row.claim_token = stage_job(db, "skill_unpack", draft_id=row.id).id
        else:
            row.status, row.stage = "queued", "reading"
        db.commit()
        return _output(db, row)
