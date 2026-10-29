"""Run-bound library discovery and Worker-text reads; never parse user files here."""

import codecs
import re
from datetime import UTC

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
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
    derivation_id: str | None = Field(default=None, max_length=40)
    content_revision: str | None = Field(default=None, max_length=64)
    anchor: str | None = Field(default=None, max_length=300)
    block_offset: int = Field(default=0, ge=0)
    query: str = Field(default="", max_length=200)
    pages: list[int] | None = Field(default=None, min_length=1, max_length=20)

    @field_validator("pages")
    @classmethod
    def valid_pages(cls, value):
        if value is not None and any(page < 1 or page > 100000 for page in value):
            raise ValueError("invalid page number")
        return value


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
    from ..models import MessageFileRef

    with user_critical_section(db, run.user_id):
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
        db.commit()
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
                "summary": str(((f.meta or {}).get("summary") or {}).get("text") or "")[:120],
                "updated_at": f.created_at.replace(tzinfo=UTC).isoformat(),
            }
            for f, project in db.execute(q)
        ]
    }


@router.post("/{run_id}/library/read", dependencies=[Depends(require_scope("files:read_for_run"))])
def read_library(run_id: str, body: Read, db: Session = Depends(get_db)):
    _, file = _ready(db, run_id, body.file_id)
    meta = file.meta or {}
    if meta.get("document_manifest_key"):
        return read_document_blocks(db, file, body)
    keys = list(filter(None, [meta.get("text_key"), meta.get("ocr_text_key") if meta.get("ocr") == "done" else None]))
    if body.pages is not None:
        return read_selected_pages(file, keys, body)
    pages = []
    current_page = None
    marker_tail = ""
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
                    # Track explicit Worker markers while streaming skipped characters, without buffering the file.
                    combined = marker_tail + text
                    base = position - len(marker_tail)
                    for match in re.finditer(r"\f\[page:(\d+)\]\n", combined):
                        boundary = base + match.end()
                        if boundary <= body.offset:
                            current_page = int(match[1])
                        elif boundary <= body.offset + 30000:
                            pages.append({"page": int(match[1]), "offset": boundary - body.offset})
                    marker_tail = combined[-40:]
                    start = max(0, body.offset - position)
                    keep += text[start : start + max(0, 30001 - len(keep))]
                    position += len(text)
                    if len(keep) >= 30001:
                        break
            if len(keep) >= 30001:
                break
    if current_page is not None:
        pages.insert(0, {"page": current_page, "offset": 0})
    pages = [dict(pair) for pair in dict.fromkeys(tuple(item.items()) for item in pages)]
    truncated = len(keep) > 30000
    return {
        "file_id": file.id,
        "filename": file.filename,
        "text": keep[:30000],
        "pages": pages,
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


def read_selected_pages(file, keys, body):
    """Select explicit physical pages while streaming, retaining only one response window."""
    selected = set(body.pages)
    keep, boundaries, position = "", [], 0
    current = None

    def take(text):
        nonlocal keep, position
        if current not in selected:
            return
        start = max(0, body.offset - position)
        piece = text[start : start + max(0, 30001 - len(keep))]
        if piece:
            if not boundaries or boundaries[-1]["page"] != current:
                boundaries.append({"page": current, "offset": len(keep)})
            keep += piece
        position += len(text)

    for key in keys:
        current, tail = None, ""
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        with get_storage().open_stream(key) as chunks:
            for chunk in chunks:
                combined = tail + decoder.decode(chunk)
                consumed = 0
                for marker in re.finditer(r"\f\[page:(\d+)\]\n", combined):
                    take(combined[consumed : marker.start()])
                    current = int(marker[1])
                    consumed = marker.end()
                rest = combined[consumed:]
                take(rest[:-40] if len(rest) > 40 else "")
                tail = rest[-40:]
                if len(keep) >= 30001:
                    break
            if len(keep) < 30001:
                take(tail + decoder.decode(b"", final=True))
        if len(keep) >= 30001:
            break
    return {
        "file_id": file.id,
        "filename": file.filename,
        "text": keep[:30000],
        "pages": [p for p in boundaries if p["offset"] < 30000],
        "truncated": len(keep) > 30000,
        "next_offset": body.offset + 30000 if len(keep) > 30000 else None,
    }


def read_document_blocks(db, file, body):
    from ..models import DocumentBlock, FileDerivation

    derivation = db.scalar(
        select(FileDerivation)
        .where(FileDerivation.file_id == file.id, FileDerivation.kind == "native")
        .order_by(FileDerivation.created_at.desc(), FileDerivation.id.desc())
        .limit(1)
    )
    if not derivation:
        raise HTTPException(409, {"code": "document_index_pending", "message": "文档索引尚未完成"})
    import hashlib

    revision = str((file.meta or {}).get("ocr_cache_revision", 0))
    if body.content_revision and body.content_revision != revision:
        raise HTTPException(409, {"code": "cursor_stale", "message": "可读文字版本已变化，请重新读取"})
    if body.derivation_id and body.derivation_id != derivation.id:
        raise HTTPException(409, {"code": "cursor_stale", "message": "文档版本已变化，请重新读取"})
    q = select(DocumentBlock).where(DocumentBlock.derivation_id == derivation.id, DocumentBlock.ordinal >= body.offset)
    if body.pages:
        if (file.meta.get("extract") or {}).get("kind") not in {"pdf", "pptx"}:
            raise HTTPException(422, "此格式请按结构anchor读取，不能使用物理页码")
        q = q.where(DocumentBlock.unit_number.in_(body.pages))
    if body.anchor:
        q = q.where(DocumentBlock.anchor == body.anchor)
    blocks = db.scalars(q.order_by(DocumentBlock.ordinal).limit(101)).all()
    from .document_sources import ocr_records

    ocr_pages = ocr_records(db, file, [block.unit_number - 1 for block in blocks])
    text, coverage, next_cursor = "", [], None
    from .document_content import unit_text, visual_info

    for block in blocks[:100]:
        key = block.text_key
        method = "native"
        content = get_storage().get_bytes(key).decode("utf-8")
        ocr_available = block.anchor.startswith("page:") and str(block.unit_number - 1) in ocr_pages
        content, method = unit_text(db, file, {"anchor": block.anchor, "unit_number": block.unit_number}, content.encode(), ocr_pages)
        digest = hashlib.sha256(content.encode()).hexdigest()
        if body.query and body.query.casefold() not in content.casefold():
            continue
        start = body.block_offset if block.ordinal == body.offset else 0
        piece = content[start : start + max(0, 8000 - len(text))]
        if piece:
            text += piece
            coverage.append(
                {
                    "block_id": block.id,
                    "anchor": block.anchor,
                    "part_start": start,
                    "part_end": start + len(piece),
                    "digest": digest,
                    "method": method,
                    "ocr_available": bool(ocr_available),
                }
            )
        if start + len(piece) < len(content):
            next_cursor = {"offset": block.ordinal, "block_offset": start + len(piece)}
            break
        if len(text) >= 8000:
            next_cursor = {"offset": block.ordinal + 1, "block_offset": 0}
            break
    if next_cursor is None and len(blocks) > 100:
        next_cursor = {"offset": blocks[100].ordinal, "block_offset": 0}
    if next_cursor:
        next_cursor["derivation_id"] = derivation.id
        next_cursor["content_revision"] = revision
    return {
        "file_id": file.id,
        "filename": file.filename,
        "text": text,
        "coverage": coverage,
        "derivation_id": derivation.id,
        "content_revision": revision,
        "status": derivation.status,
        "next_cursor": next_cursor,
        "truncated": next_cursor is not None,
        "next_offset": next_cursor["offset"] if next_cursor else None,
        "pages": [],
        "scope": "selected" if body.pages or body.anchor or body.query else "document",
        "visual": visual_info(file),
        "visual_note": "原生正文/OCR不包含图片、图表、连线和布局语义；需要时用view_attachment，非视觉模型须明确未理解图形。",
    }


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


@router.post("/{run_id}/library/image-reference", dependencies=[Depends(require_scope("files:read_for_run"))])
def image_reference(run_id: str, body: Read, db: Session = Depends(get_db)):
    _, file = _ready(db, run_id, body.file_id)
    if file.content_type.startswith("image/"):
        return {
            "files": [
                {"id": file.id, "file_id": file.id, "filename": file.filename, "content_type": file.content_type, "version": file.version}
            ]
        }
    with user_critical_section(db, file.user_id):
        db.refresh(file)
        return document_images(db, file, body.pages)


def document_images(db, file, selected, *, prepare=True):
    from ..models import RenderedPage
    from .document_content import visual_pages
    from .routes_ocr import request_page_render

    meta = file.meta or {}
    if selected is None:
        selected = visual_pages(file)
        if selected == []:
            return {"files": []}
    preview = meta.get("ocr_preview") or {}
    if preview.get("status") == "failed":
        return {"error": "文档视觉页面生成失败，请稍后重试或换用可读取文件", "reason": preview.get("reason")}
    all_pages = selected is None
    if all_pages and preview.get("status") == "ready" and preview.get("page_count"):
        selected = list(range(1, min(preview["page_count"], 20) + 1))
    pages = (
        db.scalars(
            select(RenderedPage)
            .where(RenderedPage.file_id == file.id, RenderedPage.version == "poppler-1280-v1")
            .order_by(RenderedPage.page)
            .limit(21)
        ).all()
        if selected is None
        else db.scalars(
            select(RenderedPage).where(
                RenderedPage.file_id == file.id, RenderedPage.page.in_(selected[:20]), RenderedPage.version == "poppler-1280-v1"
            )
        ).all()
    )
    by_page = {p.page: p for p in pages}
    missing = [p for p in (selected or [])[:20] if p not in by_page]
    if selected is None or missing:
        if prepare:
            request_page_render(db, file, None if selected is None else missing)
            db.commit()
        return {"pending": True, "pages": missing, "retry_after": 1}
    return {
        "files": [
            {
                "id": file.id,
                "file_id": file.id,
                "filename": file.filename,
                "content_type": "image/jpeg",
                "version": file.version,
                "page": p,
                "digest": by_page[p].digest,
            }
            for p in selected[:20]
        ],
        "truncated": len(selected) > 20 or (all_pages and preview.get("page_count", 0) > 20),
        "page_count": preview.get("page_count"),
        "location_kind": "rendered_page",
    }


@router.post("/{run_id}/library/extract-text", dependencies=[Depends(require_scope("files:read_for_run"))])
def extract_for_run(run_id: str, body: Read, db: Session = Depends(get_db)):
    run, file = _ready(db, run_id, body.file_id)
    from .routes_ocr import request_extraction

    with user_critical_section(db, run.user_id):
        db.refresh(file)
        result = request_extraction(db, file, body.pages)
        db.commit()
    if result.get("ocr") == "done":
        result["content"] = read_library(run_id, body, db)
    return result
