"""Run-bound library discovery and Worker-text reads; never parse user files here."""

import codecs
from datetime import UTC

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from ..deps import get_db
from ..locks import user_critical_section
from ..models import Conversation, FileObject, FileStatus, Project, RunStatus
from ..service_auth import require_scope
from ..storage import get_storage
from .routes_internal import _run_or_404
from .runtime_fence import fence_attempt

router = APIRouter(prefix="/internal/runs", tags=["internal"])


class Search(BaseModel):
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class Read(BaseModel):
    file_id: str = Field(max_length=40)
    offset: int = Field(default=0, ge=0, le=10_000_000)


def _ready(db, run_id, file_id):
    run = _run_or_404(db, run_id)
    file = db.get(FileObject, file_id, populate_existing=True)
    if (
        not file
        or file.user_id != run.user_id
        or file.status != FileStatus.READY
        or file.purpose != "library"
        or file.deleted_at
        or file.purged_at
    ):
        raise HTTPException(404, "资料不存在或不可用")
    return run, file


def library_query(user_id, project_id, body):
    q = (
        select(FileObject, Project.name)
        .outerjoin(Project, Project.id == FileObject.project_id)
        .where(
            FileObject.user_id == user_id,
            FileObject.status == FileStatus.READY,
            FileObject.purpose == "library",
            FileObject.deleted_at.is_(None),
            FileObject.purged_at.is_(None),
            FileObject.is_head == 1,
        )
    )
    if body.query:
        q = q.where(FileObject.filename.contains(body.query, autoescape=True))
    q = q.order_by(case((FileObject.project_id == project_id, 0), else_=1), FileObject.created_at.desc(), FileObject.id.desc()).limit(
        body.limit
    )
    return q


@router.post("/{run_id}/library/search", dependencies=[Depends(require_scope("files:read_for_run"))])
def search_library(run_id: str, body: Search, db: Session = Depends(get_db)):
    run = _run_or_404(db, run_id)
    conv = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    q = library_query(run.user_id, conv.project_id if conv else None, body)
    return {
        "files": [
            {
                "file_id": f.id,
                "filename": f.filename,
                "content_type": f.content_type,
                "size": f.size,
                "project": project,
                "updated_at": f.created_at.replace(tzinfo=UTC).isoformat(),
            }
            for f, project in db.execute(q)
        ]
    }


@router.post("/{run_id}/library/read", dependencies=[Depends(require_scope("files:read_for_run"))])
def read_library(run_id: str, body: Read, db: Session = Depends(get_db)):
    _, file = _ready(db, run_id, body.file_id)
    meta = file.meta or {}
    keys = list(filter(None, [meta.get("text_key"), meta.get("ocr_text_key") if meta.get("ocr") == "done" else None]))
    if not keys:
        text = str(meta.get("excerpt") or "")
        keep = text[body.offset : body.offset + 30001]
    else:
        # Stream/decode only as far as this page; offset is in Unicode characters, never UTF-8 bytes.
        keep, position = "", 0
        for index, key in enumerate(keys):
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            with get_storage().open_stream(key) as chunks:

                def decoded():
                    if index:
                        yield "\n"
                    for chunk in chunks:
                        yield decoder.decode(chunk)
                    yield decoder.decode(b"", final=True)

                for text in decoded():
                    start = max(0, body.offset - position)
                    keep += text[start : start + max(0, 30001 - len(keep))]
                    position += len(text)
                    if len(keep) >= 30001:
                        break
            if len(keep) >= 30001:
                break
    truncated = len(keep) > 30000
    return {
        "file_id": file.id,
        "filename": file.filename,
        "text": keep[:30000],
        "truncated": truncated,
        "next_offset": body.offset + 30000 if truncated else None,
    }


@router.post("/{run_id}/library/authorize-copy", dependencies=[Depends(require_scope("runs:status"))])
def authorize_copy(run_id: str, body: Read, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    run, file = _ready(db, run_id, body.file_id)
    with user_critical_section(db, run.user_id):
        fence_attempt(db, run.id, x_run_attempt)
        db.refresh(run)
        if (run.options or {}).get("mode", "work") == "chat" or run.status != RunStatus.RUNNING:
            raise HTTPException(422, "当前轮次不能复制到工作区")
        ids = set((run.runtime_meta or {}).get("library_file_ids") or [])
        if file.id not in ids and len(ids) >= 50:
            raise HTTPException(422, "每轮最多复制50份资料")
        ids.add(file.id)
        run.runtime_meta = {**(run.runtime_meta or {}), "library_file_ids": sorted(ids)}
        db.commit()
    return {"file_id": file.id, "filename": file.filename}
