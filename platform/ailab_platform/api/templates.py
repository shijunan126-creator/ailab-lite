"""Template visibility and immutable per-run selection, owned by the API."""

from pathlib import PurePath

from fastapi import HTTPException

from ..config import settings
from ..models import FileObject, FileStatus, Template


def is_admin(user):
    return "platform_admin" in user.principal.roles


def visible_template(db, template_id, user, edit=False):
    row = db.get(Template, template_id, populate_existing=True)
    if not row or row.deleted_at or (row.scope == "personal" and row.owner_id != user.id):
        raise HTTPException(404, "模板不存在")
    if edit and row.scope == "company" and not is_admin(user):
        raise HTTPException(403, "仅平台管理员可管理公司模板")
    return row


def template_status(file):
    if (file.meta or {}).get("template_error"):
        return "failed", "模板无法解析，请检查文件后重新上传"
    if file.status == FileStatus.REJECTED:
        return "rejected", "文件未通过安全扫描"
    return file.status, None


def freeze_template(db, user, selection, mode):
    if selection is None:
        return None
    if mode != "work":
        raise HTTPException(400, "仅工作模式可使用模板")
    selection = selection.model_dump(exclude_none=True) if hasattr(selection, "model_dump") else selection
    if len(selection) != 1 or not set(selection) <= {"id", "file_id"}:
        raise HTTPException(400, "请选择一个模板或资料库文件")
    if "id" in selection:
        row = visible_template(db, selection["id"], user)
        file = db.get(FileObject, row.file_id)
        if not file or template_status(file)[0] != "ready" or file.purged_at:
            raise HTTPException(400, "模板尚未就绪")
        return {"source": "template", "id": row.id, "file_id": file.id, "name": row.name}
    file = db.get(FileObject, selection["file_id"], populate_existing=True)
    if not file or file.user_id != user.id or file.purpose != "library" or file.deleted_at or file.purged_at:
        raise HTTPException(400, "模板文件不存在")
    if (
        file.status != FileStatus.READY
        or not (file.meta or {}).get("scan", {}).get("ok")
        or PurePath(file.filename).suffix.lower() not in {".pptx", ".potx"}
        or "macro" in file.content_type.lower()
        or file.size > settings.max_template_bytes
    ):
        raise HTTPException(400, "请选择已通过安全扫描且大小符合限制的 PPTX/POTX 文件")
    return {"source": "file", "id": None, "file_id": file.id, "name": PurePath(file.filename).stem[:60]}
