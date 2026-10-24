"""Platform-admin brand settings and bounded scanned asset uploads."""

import hashlib
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from ..audit import AuditedRoute, record_audit
from ..config import settings
from ..deps import current_user, get_db
from ..library import assign_file_version
from ..models import FileObject, FileStatus, User, new_id
from ..storage import get_storage
from .brand import KINDS, MAX_TEMPLATE, brand_lock, brand_settings, save_brand
from .jobs import stage_job
from .routes_admin import _context_admin
from .routes_files import _ClosingStreamingResponse, safe_filename

router = APIRouter(prefix="/v1/admin/brand", tags=["admin"], route_class=AuditedRoute)
Kind = Literal["logo", "pptx", "docx"]


class Colors(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primary: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    accent: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")


class BrandPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    name: str | None = Field(default=None, max_length=120)
    colors: Colors | None = None
    font: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def nonnull(self):
        if not self.model_fields_set or any(getattr(self, k) is None for k in self.model_fields_set):
            raise ValueError("non-null changes required")
        return self


def asset_out(file):
    meta = file.meta or {}
    analysis = meta.get("brand")
    if not analysis and file.status == FileStatus.REJECTED:
        analysis = {"error": meta.get("brand_error") or (meta.get("scan") or {}).get("verdict") or "analysis_failed"}
    return {
        "file_id": file.id,
        "filename": file.filename,
        "status": file.status,
        "size": file.size,
        "analysis": analysis,
        "thumbnail_url": f"/api/v1/admin/brand/assets/{file.meta['brand_kind']}/thumbnail" if (file.meta or {}).get("thumb_key") else None,
    }


def output(db):
    value = brand_settings(db)
    assets = {}
    for kind, field in KINDS.items():
        # Display the new pending/rejected candidate while the effective ready id stays unchanged.
        file_id = value.get("pending", {}).get(kind) or value.get(field)
        file = db.get(FileObject, file_id) if file_id else None
        assets[kind] = asset_out(file) if file else None
    return {**{k: v for k, v in value.items() if k != "pending"}, "assets": assets}


@router.get("")
def get_brand(user: User = Depends(current_user), db: Session = Depends(get_db)):
    _context_admin(user)
    return output(db)


@router.patch("")
def patch_brand(body: BrandPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _context_admin(user)
    with brand_lock(db):
        value = brand_settings(db)
        value.update(body.model_dump(exclude_unset=True))
        save_brand(db, value)
        record_audit(db, "settings.brand.update", "settings", "brand", actor_id=user.id, detail={"fields": sorted(body.model_fields_set)})
        db.commit()
        return output(db)


@router.post("/assets/{kind}", status_code=201)
def upload_asset(kind: Kind, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    _context_admin(user)
    suffix = Path(file.filename or "").suffix.lower()
    allowed = {"logo": {".png", ".jpg", ".jpeg"}, "pptx": {".pptx"}, "docx": {".docx"}}
    if suffix not in allowed[kind]:
        raise HTTPException(422, "仅接受 PNG/JPEG 标志、PPTX 或 DOCX 模板；SVG 暂不支持")
    limit = min(settings.max_upload_bytes, settings.max_parse_bytes, 2 * 1024 * 1024 if kind == "logo" else MAX_TEMPLATE)
    size = 0
    digest = hashlib.sha256()
    file_id = new_id("fil")
    filename = safe_filename(file.filename or kind)
    key = f"brand/{file_id}/{filename}"
    storage = get_storage()
    with tempfile.NamedTemporaryFile(prefix="ailab-brand-") as tmp:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise HTTPException(413, f"asset exceeds {limit} bytes")
            digest.update(chunk)
            tmp.write(chunk)
        if not size:
            raise HTTPException(422, "empty asset")
        tmp.flush()
        storage.put_file(key, tmp.name, file.content_type or "application/octet-stream")
        try:
            with brand_lock(db):
                row = FileObject(
                    id=file_id,
                    user_id=user.id,
                    filename=filename,
                    purpose="brand",
                    size=size,
                    sha256=digest.hexdigest(),
                    storage_key=key,
                    content_type=file.content_type or "application/octet-stream",
                    status=FileStatus.PENDING,
                    meta={"brand_kind": kind, "processing": "queued"},
                )
                assign_file_version(db, row)
                db.add(row)
                db.flush()
                value = brand_settings(db)
                value.setdefault("pending", {})[kind] = row.id
                # An upload changes only its candidate; effective version advances on successful activation.
                from ..models import PlatformSetting

                setting = db.get(PlatformSetting, "brand")
                if setting:
                    setting.value = value
                else:
                    db.add(PlatformSetting(key="brand", value=value))
                stage_job(db, "file", file_id=row.id)
                record_audit(db, "settings.brand.upload", "file", row.id, actor_id=user.id, detail={"kind": kind})
                db.commit()
                return asset_out(row)
        except BaseException:
            storage.delete(key)
            raise


@router.delete("/assets/{kind}", status_code=204)
def delete_asset(kind: Kind, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _context_admin(user)
    with brand_lock(db):
        value = brand_settings(db)
        value.pop(KINDS[kind], None)
        value.get("pending", {}).pop(kind, None)
        save_brand(db, value)
        record_audit(db, "settings.brand.delete", "settings", "brand", actor_id=user.id, detail={"kind": kind})
        db.commit()


@router.get("/assets/{kind}/{view}")
def asset_content(kind: Kind, view: Literal["content", "thumbnail"], user: User = Depends(current_user), db: Session = Depends(get_db)):
    _context_admin(user)
    value = brand_settings(db)
    file_id = value.get("pending", {}).get(kind) or value.get(KINDS[kind])
    file = db.get(FileObject, file_id) if file_id else None
    if not file or file.status != FileStatus.READY:
        raise HTTPException(404, "asset not ready")
    key = (file.meta or {}).get("thumb_key") if view == "thumbnail" else file.storage_key
    if not key:
        raise HTTPException(404, "thumbnail unavailable")
    media = "image/webp" if view == "thumbnail" else file.content_type
    db.close()
    return _ClosingStreamingResponse(get_storage().stream(key), media_type=media)
