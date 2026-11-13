"""Run-bound library discovery, attachment resolution and copy authorization; never parse user files here."""

from datetime import UTC

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session

from ..deps import get_db
from ..locks import user_critical_section
from ..models import Conversation, FileObject, FileStatus, Project, RunStatus
from ..service_auth import require_scope
from .routes_internal import _run_or_404
from .runtime_fence import fence_attempt

router = APIRouter(prefix="/internal/runs", tags=["internal"])


class Search(BaseModel):
    query: str = Field(default="", max_length=200)
    limit: int = Field(default=10, ge=1, le=20)


class FileRef(BaseModel):
    file_id: str = Field(max_length=40)


def authorized_source(db, run, file_id):
    """Authorize and bind a source in the caller's existing user/fence transaction."""
    from ..models import MessageFileRef

    conversation = db.get(Conversation, run.conversation_id, populate_existing=True) if run.conversation_id else None
    if run.conversation_id and (conversation is None or conversation.deleted_at):
        raise HTTPException(404, "对话不存在")
    file = db.get(FileObject, file_id, populate_existing=True)
    if (
        not file
        or file.user_id != run.user_id
        or file.status != FileStatus.READY
        or file.purpose != "library"
        or file.deleted_at
        or file.purged_at
        or not in_scope(db, run, file)
    ):
        raise HTTPException(404, "资料不存在或不可用")

    existing = db.scalar(
        select(MessageFileRef.id).where(
            MessageFileRef.run_id == run.id,
            MessageFileRef.message_kind == "run_prompt",
            MessageFileRef.message_id == run.id,
            MessageFileRef.file_id == file.id,
            MessageFileRef.role == "agent_read",
        )
    )
    if not existing:
        db.add(
            MessageFileRef(
                run_id=run.id,
                message_kind="run_prompt",
                message_id=run.id,
                file_id=file.id,
                role="agent_read",
                ordinal=0,
                reference_origin="agent_read",
            )
        )
    return file


def in_scope(db, run, file) -> bool:
    """An isolated project's files stay inside it, and outside files stay out, unless the user attached one here."""
    from ..memory_recall.policy import project_mode, run_project
    from ..models import MessageFileRef, Run

    pid = run_project(db, run)
    if project_mode(db, pid) == "isolated":
        allowed = file.project_id == pid
    else:
        allowed = not file.project_id or file.project_id == pid or project_mode(db, file.project_id) != "isolated"
    if allowed:
        return True
    return (
        db.scalar(
            select(MessageFileRef.id)
            .join(Run, Run.id == MessageFileRef.run_id)
            .where(
                MessageFileRef.file_id == file.id,
                MessageFileRef.role == "input",
                Run.user_id == run.user_id,
                Run.conversation_id == run.conversation_id if run.conversation_id else Run.id == run.id,
            )
            .limit(1)
        )
        is not None
    )


def _ready(db, run_id, file_id):
    run = _run_or_404(db, run_id)
    with user_critical_section(db, run.user_id):
        db.refresh(run)
        file = authorized_source(db, run, file_id)
        db.commit()
    return run, file


def library_query(user_id, project_id, body, isolated=False):
    """Own project first. Inside an isolated project only its files; elsewhere never an isolated project's files."""
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
    if isolated:
        q = q.where(FileObject.project_id == project_id)
    else:
        q = q.where(or_(FileObject.project_id.is_(None), Project.memory_mode != "isolated"))
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
    from ..memory_recall.policy import project_mode

    pid = conv.project_id if conv and not conv.deleted_at else None
    q = library_query(run.user_id, pid, body, isolated=project_mode(db, pid) == "isolated")
    return {
        "files": [
            {
                "file_id": f.id,
                "filename": f.filename,
                "content_type": f.content_type,
                "size": f.size,
                "project": project,
                "summary": str(((f.meta or {}).get("summary") or {}).get("text") or "")[:120],
                "updated_at": f.created_at.replace(tzinfo=UTC).isoformat(),
            }
            for f, project in db.execute(q)
        ]
    }


@router.post("/{run_id}/library/authorize-copy", dependencies=[Depends(require_scope("runs:status"))])
def authorize_copy(run_id: str, body: FileRef, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
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


class Resolve(BaseModel):
    message_id: str | None = Field(default=None, max_length=40)
    relative: str | None = Field(default=None, pattern="^(current|previous)$")
    kind: str | None = Field(default=None, pattern="^(image|file)$")
    ordinal: int | None = Field(default=None, ge=1, le=50)
    filename: str | None = Field(default=None, max_length=200)


@router.post("/{run_id}/library/resolve", dependencies=[Depends(require_scope("files:read_for_run"))])
def resolve_attachment(run_id: str, body: Resolve, db: Session = Depends(get_db)):
    from ..conversation_tree import ancestor_query
    from ..models import Run
    from .document_sources import attachment_manifest

    run = _run_or_404(db, run_id)
    if body.relative == "current":
        candidates = [run]
    else:
        path = ancestor_query(run.conversation_id, run.parent_run_id)
        q = select(Run).join(path, Run.id == path.c.id).where(Run.user_id == run.user_id)
        if body.message_id:
            q = q.where(Run.id == body.message_id)
        candidates = list(db.scalars(q.order_by(path.c.depth).limit(1 if body.relative == "previous" else 5)))
        if not body.relative and (not body.message_id or body.message_id == run.id):
            candidates = [run, *candidates][:5]
    from ..models import AppliedMessagePosition, QueuedMessage

    refs = []
    if body.relative == "previous":
        candidates = [run, *candidates]
    messages = []
    for message in candidates:
        positions = db.scalars(
            select(AppliedMessagePosition)
            .where(AppliedMessagePosition.run_id == message.id)
            .order_by(AppliedMessagePosition.application_seq.desc())
            .limit(6)
        ).all()
        if not positions:
            messages.append((message, "run_prompt", message.id, None))
        for position in positions:
            if body.relative == "previous" and message.id == run.id and position.message_kind == "run_prompt":
                continue
            if body.message_id and body.message_id not in {position.message_id, position.alias_run_prompt_id}:
                continue
            queued = db.get(QueuedMessage, position.message_id) if position.message_kind == "queued_message" else None
            messages.append((message, position.message_kind, position.message_id, queued.file_ids if queued else []))
    if body.relative == "previous":
        messages = messages[:1]
    for message, kind, message_id, file_ids in messages:
        items = attachment_manifest(db, message, file_ids=file_ids, message_kind=kind, message_id=message_id)
        if body.kind == "image":
            items = [r for r in items if (r["content_type"] or "").startswith("image/")]
        if body.ordinal:
            items = items[body.ordinal - 1 : body.ordinal]
        if body.filename:
            items = [r for r in items if body.filename in r["filename"]]
        refs.extend(items)
    return {"status": "resolved" if len(refs) == 1 else "ambiguous" if refs else "unavailable", "candidates": refs[:5]}
