"""Upload identity under the API user lock; operation receipts never resurrect files."""

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import func, select

from ..models import FileObject, FileStatus, UploadReceipt
from .serializers import file_dict


def fingerprint(**fields):
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def replay(db, receipt, digest):
    if receipt.fingerprint != digest:
        raise HTTPException(422, "上传操作标识已用于其他内容，请重新选择文件")
    if receipt.state != "completed":
        raise HTTPException(410, "上传预约已失效，请重新选择文件")
    file = db.get(FileObject, receipt.file_id, populate_existing=True)
    if file is None or file.user_id != receipt.user_id or file.deleted_at is not None or file.purged_at is not None:
        raise HTTPException(410, "原上传文件已删除，请重新选择文件")
    return {**file_dict(file), "reused": bool(receipt.reused)}


def reusable(db, user_id, project_id, filename, content_type, size, sha256):
    # ix_files_user_sha bounds candidates before project/name filtering, including renamed display names.
    return db.scalar(
        select(FileObject)
        .where(
            FileObject.user_id == user_id,
            FileObject.sha256 == sha256,
            FileObject.status == FileStatus.READY,
            FileObject.deleted_at.is_(None),
            FileObject.purged_at.is_(None),
            FileObject.is_head == 1,
            FileObject.project_id == project_id,
            FileObject.purpose == "library",
            FileObject.content_type == content_type,
            FileObject.size == size,
            func.coalesce(FileObject.meta["upload_filename"].as_string(), FileObject.filename) == filename,
        )
        .order_by(FileObject.id)
        .limit(1)
    )


def receipt_for(db, user_id, operation, digest, file_id, *, filename=None, reused=False, reservation_id=None, state="completed"):
    if operation:
        db.add(
            UploadReceipt(
                user_id=user_id,
                client_upload_id=operation,
                fingerprint=digest,
                filename=filename,
                file_id=file_id,
                reused=int(reused),
                reservation_id=reservation_id,
                state=state,
            )
        )
