"""API authorization and version-bound viewer indexes and selections."""

import json
import unicodedata
from pathlib import PurePath

from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from ..contracts.document_view import PARSER_REVISION, DocRef
from ..models import FileObject, MessageDocRef, MessageFileRef
from ..storage import get_storage


def index(file):
    kind = PurePath(file.filename).suffix.lower().removeprefix(".")
    base = {"file_id": file.id, "version": file.sha256, "parser_revision": PARSER_REVISION}
    if kind not in {"docx", "xlsx", "pptx", "pdf"}:
        return {**base, "status": "unsupported", "native": False}
    storage = get_storage()
    key = f"derived/{file.id}/viewer.json"
    if storage.exists(key) and storage.size(key) <= 4 * 1024**2:
        cached = json.loads(storage.get_bytes(key))
        if cached.get("version") == file.sha256 and cached.get("parser_revision") == PARSER_REVISION:
            return cached
    processing = (file.meta or {}).get("processing")
    return {**base, "kind": kind, "status": "pending" if processing not in {"done", "rejected"} else "failed", "native": False}


def normalized(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def checked_ranges(file, locations):
    import httpx

    from ..config import settings

    try:
        with httpx.Client(timeout=httpx.Timeout(25, connect=3), trust_env=False) as client:
            response = client.post(
                f"{settings.worker_base_url}/internal/viewer/check",
                headers={"X-Service-Token": settings.caller_token("api")},
                json={"file_id": file.id, "version": file.sha256, "selections": locations},
            )
    except httpx.HTTPError:
        raise HTTPException(503, "document validation unavailable") from None
    if response.status_code != 200:
        raise HTTPException(response.status_code, "document locator unavailable")
    result = response.json()
    if result.get("version") != file.sha256 or len(result.get("selections", [])) != len(locations):
        raise HTTPException(409, "document version changed")
    return result["selections"]


def validate_refs(db, user, refs):
    from .routes_files import _ready_file

    result = []
    parsed = [
        DocRef.model_validate(
            r.model_dump(exclude_none=True) if hasattr(r, "model_dump") else {k: v for k, v in r.items() if k != "filename"}
        )
        for r in refs or []
    ]
    ranges = {}
    for file_id in dict.fromkeys(r.file_id for r in parsed if r.file_id):
        file = _ready_file(db, file_id, user)
        if file.deleted_at or file.purpose != "library":
            raise HTTPException(404, "document unavailable")
        selected = [r for r in parsed if r.file_id == file_id]
        if any(r.version != file.sha256 for r in selected):
            raise HTTPException(409, "document version changed")
        checked = checked_ranges(file, [r.at for r in selected])
        ranges.update({(file_id, r.at): item for r, item in zip(selected, checked, strict=True)})
    for ref in parsed:
        ref = DocRef.model_validate(
            ref.model_dump(exclude_none=True) if hasattr(ref, "model_dump") else {k: v for k, v in ref.items() if k != "filename"}
        )
        target = ref.model_dump(exclude_none=True)
        if ref.file_id:
            file = _ready_file(db, ref.file_id, user)
            if file.deleted_at or file.purpose != "library":
                raise HTTPException(404, "document unavailable")
            if file.sha256 != ref.version:
                raise HTTPException(409, "document version changed")
            item = ranges[(file.id, ref.at)]
            text = item["text"]
            target["at"] = item["at"]
            filename = file.filename
        else:
            from .workspace_documents import workspace_reference

            filename, text = workspace_reference(db, user, ref)
        quote = normalized(ref.quote)
        if not quote or quote not in normalized(text):
            raise HTTPException(422, "quote does not match document range")
        result.append({**target, "filename": filename})
    return result


def bind(db, kind, message_id, refs, run_id=None):
    if len(refs) > 8:
        raise HTTPException(422, "too many document references")
    for ordinal, ref in enumerate(refs):
        db.add(
            MessageDocRef(
                run_id=run_id,
                message_kind=kind,
                message_id=message_id,
                ordinal=ordinal,
                file_id=ref.get("file_id"),
                target={k: v for k, v in ref.items() if k != "filename"},
                filename=ref["filename"],
            )
        )
        # Register as a source (not an ordinary attachment): deletion and access revocation invalidate context.
        fid = ref.get("file_id")
        if fid and not db.scalar(
            select(MessageFileRef.id)
            .where(
                MessageFileRef.message_kind == kind,
                MessageFileRef.message_id == message_id,
                MessageFileRef.file_id == fid,
                MessageFileRef.role == "agent_read",
            )
            .limit(1)
        ):
            db.add(
                MessageFileRef(
                    run_id=run_id,
                    message_kind=kind,
                    message_id=message_id,
                    file_id=fid,
                    ordinal=ordinal + 1,
                    role="agent_read",
                    filename_snapshot=ref["filename"],
                )
            )
            db.flush()


def output_map(db, identities, *, visible=True):
    by_kind = {}
    for kind, ident in identities:
        by_kind.setdefault(kind, []).append(ident)
    if not by_kind:
        return {}
    query = (
        select(MessageDocRef)
        .outerjoin(FileObject, FileObject.id == MessageDocRef.file_id)
        .where(or_(*(and_(MessageDocRef.message_kind == kind, MessageDocRef.message_id.in_(ids)) for kind, ids in by_kind.items())))
    )
    if visible:
        query = query.where(
            or_(
                MessageDocRef.file_id.is_(None),
                and_(
                    FileObject.id.is_not(None),
                    FileObject.deleted_at.is_(None),
                    FileObject.purged_at.is_(None),
                    FileObject.status == "ready",
                ),
            )
        )
    query = query.order_by(MessageDocRef.message_kind, MessageDocRef.message_id, MessageDocRef.ordinal).limit(8 * len(identities))
    result = {}
    for ref in db.scalars(query):
        result.setdefault((ref.message_kind, ref.message_id), []).append({**ref.target, "filename": ref.filename})
    return result


def output(db, kind, message_id, *, visible=True):
    return output_map(db, [(kind, message_id)], visible=visible).get((kind, message_id), [])
