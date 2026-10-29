"""API-owned attachment identities and trusted derivative index ingestion."""

import hashlib
import json
import re

from fastapi import HTTPException
from sqlalchemy import insert, select

from ..models import DocumentBlock, FileDerivation, FileObject, MessageFileRef
from ..storage import get_storage


def bind_refs(db, message_kind, message_id, file_ids, run_id=None):
    existing = set(
        db.scalars(
            select(MessageFileRef.file_id).where(
                MessageFileRef.message_kind == message_kind, MessageFileRef.message_id == message_id, MessageFileRef.role == "input"
            )
        )
    )
    for ordinal, file_id in enumerate(dict.fromkeys(file_ids or []), 1):
        if file_id not in existing:
            file = db.get(FileObject, file_id)
            provenance = dict((file.meta or {}).get("upload_provenance") or {}) if file else {}
            origin = "library"
            if provenance.get("id") and not provenance.get("first_message_id"):
                origin = "upload"
                provenance["first_message_id"] = message_id
                file.meta = {**(file.meta or {}), "upload_provenance": provenance}
            db.add(
                MessageFileRef(
                    run_id=run_id,
                    message_kind=message_kind,
                    message_id=message_id,
                    file_id=file_id,
                    ordinal=ordinal,
                    role="input",
                    reference_origin=origin,
                    upload_id=provenance.get("id") if origin == "upload" else None,
                    filename_snapshot=file.filename if file else None,
                )
            )


def attachment_manifest(db, run, *, file_ids=None, message_id=None, message_kind="run_prompt"):
    message_id = message_id or run.id
    refs = db.scalars(
        select(MessageFileRef)
        .where(
            MessageFileRef.run_id == run.id,
            MessageFileRef.message_kind == message_kind,
            MessageFileRef.message_id == message_id,
            MessageFileRef.role == "input",
        )
        .order_by(MessageFileRef.ordinal)
        .limit(50)
    ).all()
    ordered = (
        [(r.ordinal, r.file_id) for r in refs]
        if refs
        else list(enumerate(file_ids if file_ids is not None else run.input_file_ids or [], 1))
    )
    snapshots = {r.file_id: r.filename_snapshot for r in refs if r.filename_snapshot}
    files = {
        f.id: f
        for f in db.scalars(
            select(FileObject).where(
                FileObject.id.in_([fid for _, fid in ordered]),
                FileObject.user_id == run.user_id,
                FileObject.deleted_at.is_(None),
                FileObject.purged_at.is_(None),
            )
        )
    }
    return [
        {
            "message_kind": message_kind,
            "message_id": message_id,
            "ordinal": ordinal,
            "file_id": fid,
            "version": files[fid].version,
            "filename": snapshots.get(fid) or files[fid].filename,
            "content_type": files[fid].content_type,
        }
        for ordinal, fid in ordered
        if fid in files
    ]


def index_document(db, file, key):
    prefix = key.rsplit("/", 1)[0] + "/"
    if not re.fullmatch(r"derived/" + re.escape(file.id) + r"/office/[a-zA-Z0-9_-]{1,100}/manifest\.json", key):
        raise HTTPException(422, "invalid document manifest")
    storage = get_storage()
    if storage.size(key) > 8 * 1024 * 1024:
        raise HTTPException(413, "document index too large")
    manifest = json.loads(storage.get_bytes(key))
    fingerprint = hashlib.sha256((file.sha256 + manifest["parser"]).encode()).hexdigest()
    old = db.scalar(
        select(FileDerivation).where(
            FileDerivation.file_id == file.id, FileDerivation.kind == "native", FileDerivation.fingerprint == fingerprint
        )
    )
    if old:
        return old
    units = manifest["units"]
    if len(units) > 100000 or manifest["status"] not in {"ready", "partial"}:
        raise HTTPException(422, "invalid document coverage")
    derivation = FileDerivation(
        file_id=file.id,
        kind="native",
        fingerprint=fingerprint,
        parser_version=manifest["parser"],
        status=manifest["status"],
        manifest_key=key,
    )
    db.add(derivation)
    db.flush()
    batch = []
    from ..models import new_id

    for ordinal, unit in enumerate(units):
        if (
            unit["ordinal"] != ordinal
            or not re.fullmatch(r"\d{6}\.txt", unit["name"])
            or len(unit["anchor"]) > 300
            or not 0 <= unit["chars"] <= 100000
        ):
            raise HTTPException(422, "invalid document block")
        batch.append(
            {
                "id": new_id("blk"),
                "derivation_id": derivation.id,
                "ordinal": ordinal,
                "unit_number": unit["unit_number"],
                "anchor": unit["anchor"],
                "text_key": prefix + unit["name"],
                "digest": unit["sha256"],
                "chars": unit["chars"],
            }
        )
        if len(batch) == 256:
            db.execute(insert(DocumentBlock), batch)
            batch.clear()
    if batch:
        db.execute(insert(DocumentBlock), batch)
    return derivation


def history_manifests(db, rows):
    refs = list(
        db.scalars(
            select(MessageFileRef)
            .where(
                MessageFileRef.run_id.in_([r.id for r in rows]), MessageFileRef.message_kind == "run_prompt", MessageFileRef.role == "input"
            )
            .order_by(MessageFileRef.run_id, MessageFileRef.ordinal)
        )
    )
    by_run = {}
    for ref in refs:
        by_run.setdefault(ref.run_id, []).append(ref)
    ids = set(ref.file_id for ref in refs)
    for row in rows:
        ids.update(row.input_file_ids or [])
    files = {
        file.id: file
        for file in db.scalars(
            select(FileObject).where(FileObject.id.in_(ids), FileObject.deleted_at.is_(None), FileObject.purged_at.is_(None))
        )
    }
    result = {}
    for row in rows:
        ordered = [(ref.ordinal, ref.file_id, ref.filename_snapshot) for ref in by_run.get(row.id, [])]
        if not ordered:
            ordered = [(n, fid, None) for n, fid in enumerate(row.input_file_ids or [], 1)]
        result[row.id] = [
            {
                "message_kind": "run_prompt",
                "message_id": row.id,
                "ordinal": n,
                "file_id": fid,
                "filename": name or files[fid].filename,
                "version": files[fid].version,
                "content_type": files[fid].content_type,
            }
            for n, fid, name in ordered
            if fid in files and files[fid].user_id == row.user_id
        ]
    return result


def ocr_records(db, file, pages):
    from ..config import settings
    from ..models import OcrPageResult

    result = {}
    if db is not None and pages:
        q = select(OcrPageResult).where(OcrPageResult.file_id == file.id, OcrPageResult.page.in_(pages))
        if settings.ocr_engine_fingerprint:
            q = q.where(OcrPageResult.fingerprint == settings.ocr_engine_fingerprint)
        for row in db.scalars(q.limit(len(pages) * 2)):
            result[str(row.page)] = {
                "generation": row.generation,
                "fingerprint": row.fingerprint,
                "text_key": row.text_key,
                "engine": row.engine,
                "backend": row.backend,
                "seconds": row.seconds,
                "pages": 1,
            }
    for page in pages:
        if str(page) not in result and str(page) in ((file.meta or {}).get("ocr_pages") or {}):
            record = file.meta["ocr_pages"][str(page)]
            if not settings.ocr_engine_fingerprint or record.get("fingerprint") == settings.ocr_engine_fingerprint:
                result[str(page)] = record
    return result
