"""Bounded personal/company PPT template registry; bytes survive soft deletion."""

import hashlib
import tempfile
from datetime import datetime
from pathlib import PurePath
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..deps import current_user, get_db
from ..library import assign_file_version
from ..locks import user_critical_section
from ..models import FileObject, FileStatus, Template, User, new_id, utcnow
from ..quota import enforce_library_quota
from ..schemas import UTCModel
from ..storage import get_storage
from .brand import brand_lock
from .jobs import stage_job
from .routes_files import _ClosingStreamingResponse, safe_filename
from .templates import is_admin, template_status, visible_template

router = APIRouter(prefix="/v1/templates", tags=["templates"])


class TemplateOut(UTCModel):
    id: str
    kind: Literal["pptx"]
    scope: Literal["company", "personal"]
    name: str
    status: Literal["pending", "ready", "rejected", "failed"]
    reason: str | None
    thumbnail_url: str | None
    layouts: int | None
    slides: int | None
    size: int
    filename: str
    created_at: datetime
    can_edit: bool


class TemplateList(BaseModel):
    company: list[TemplateOut]
    personal: list[TemplateOut]


class Rename(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name", mode="before")
    @classmethod
    def strip(cls, value):
        return value.strip() if isinstance(value, str) else value


def output(db, row, user, file=None):
    file = file or db.get(FileObject, row.file_id)
    meta = file.meta or {}
    analysis = meta.get("template") or meta.get("brand") or {}
    status, reason = template_status(file)
    return {
        "id": row.id,
        "kind": row.kind,
        "scope": row.scope,
        "name": row.name,
        "status": status,
        "reason": reason,
        "thumbnail_url": f"/v1/templates/{row.id}/thumbnail" if status == "ready" and meta.get("thumb_key") else None,
        "layouts": len(analysis["layouts"]) if "layouts" in analysis else None,
        "slides": analysis.get("slides"),
        "size": file.size,
        "filename": file.filename,
        "created_at": row.created_at,
        "can_edit": row.owner_id == user.id if row.scope == "personal" else is_admin(user),
    }


@router.get("", response_model=TemplateList)
def list_templates(kind: Literal["pptx"] = "pptx", user: User = Depends(current_user), db: Session = Depends(get_db)):
    result = {}
    for scope, limit in [("company", settings.max_company_templates), ("personal", settings.max_personal_templates)]:
        query = (
            select(Template, FileObject)
            .join(FileObject, Template.file_id == FileObject.id)
            .where(Template.scope == scope, Template.kind == kind, Template.deleted_at.is_(None))
        )
        if scope == "personal":
            query = query.where(Template.owner_id == user.id)
        rows = db.execute(query.order_by(Template.created_at.desc(), Template.id.desc()).limit(limit)).all()
        result[scope] = [output(db, row, user, file) for row, file in rows]
    return result


@router.post("", response_model=TemplateOut, status_code=201)
def upload_template(
    upload: UploadFile = File(...),
    name: str | None = Form(None),
    scope: str = Form("personal"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if scope not in {"personal", "company"}:
        raise HTTPException(400, "模板范围无效")
    if scope == "company" and not is_admin(user):
        raise HTTPException(403, "仅平台管理员可上传公司模板")
    filename = safe_filename(upload.filename or "template.pptx")
    if PurePath(filename).suffix.lower() not in {".pptx", ".potx"} or "macro" in (upload.content_type or "").lower():
        raise HTTPException(400, "仅支持无宏的 PPTX/POTX 模板")
    name = (name if name is not None else PurePath(filename).stem).strip()
    if not name or len(name) > 60:
        raise HTTPException(400, "模板名称须为1至60个字符")
    file_id = new_id("fil")
    key = f"templates/{file_id}/{filename}"
    storage = get_storage()
    size, digest = 0, hashlib.sha256()
    with tempfile.NamedTemporaryFile(prefix="ailab-template-") as tmp:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > settings.max_template_bytes:
                raise HTTPException(413, "模板文件超过大小限制")
            digest.update(chunk)
            tmp.write(chunk)
        if not size:
            raise HTTPException(400, "模板文件不能为空")
        tmp.flush()
        # Company admission shares the stable platform lock; personal admission shares the quota lock.
        with brand_lock(db) if scope == "company" else user_critical_section(db, user.id):
            query = select(func.count()).select_from(Template).where(Template.scope == scope, Template.deleted_at.is_(None))
            if scope == "personal":
                query = query.where(Template.owner_id == user.id)
                enforce_library_quota(db, user.id, size)
            limit = settings.max_company_templates if scope == "company" else settings.max_personal_templates
            if db.scalar(query) >= limit:
                raise HTTPException(400, f"最多可保存{limit}个模板")
            storage.put_file(key, tmp.name, upload.content_type or "application/octet-stream")
            try:
                file = FileObject(
                    id=file_id,
                    user_id=user.id,
                    filename=filename,
                    purpose="template",
                    size=size,
                    sha256=digest.hexdigest(),
                    storage_key=key,
                    content_type=upload.content_type or "application/octet-stream",
                    status=FileStatus.PENDING,
                    meta={"template_kind": "pptx", "processing": "queued"},
                )
                assign_file_version(db, file)
                db.add(file)
                db.flush()
                row = Template(
                    kind="pptx",
                    scope=scope,
                    owner_id=user.id if scope == "personal" else None,
                    name=name,
                    file_id=file_id,
                    created_by=user.id,
                )
                db.add(row)
                stage_job(db, "file", file_id=file_id)
                db.commit()
                return output(db, row, user, file)
            except BaseException:
                db.rollback()
                storage.delete(key)
                raise


@router.get("/{template_id}", response_model=TemplateOut)
def get_template(template_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return output(db, visible_template(db, template_id, user), user)


@router.patch("/{template_id}", response_model=TemplateOut)
def rename_template(template_id: str, body: Rename, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = visible_template(db, template_id, user, edit=True)
    row.name, row.updated_at = body.name, utcnow()
    db.commit()
    return output(db, row, user)


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = visible_template(db, template_id, user, edit=True)
    with brand_lock(db) if row.scope == "company" else user_critical_section(db, user.id):
        row = visible_template(db, template_id, user, edit=True)
        row.deleted_at = row.updated_at = utcnow()
        db.commit()


@router.get("/{template_id}/thumbnail")
def thumbnail(template_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    row = visible_template(db, template_id, user)
    file = db.get(FileObject, row.file_id)
    key = (file.meta or {}).get("thumb_key")
    if template_status(file)[0] != "ready" or not key or not key.startswith(f"derived/{file.id}/"):
        raise HTTPException(404, "缩略图尚未就绪")
    headers = {"ETag": f'"{file.sha256}-template-v1"', "Cache-Control": "private, max-age=31536000, immutable"}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    db.close()
    return _ClosingStreamingResponse(get_storage().stream(key), media_type="image/webp", headers=headers)
