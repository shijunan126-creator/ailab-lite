"""Bounded original-source revalidation with an API-owned, resumable consumption queue."""

import codecs
import hashlib
import json

from sqlalchemy import select

from ..contracts.context_summary import ContextSummary
from ..memory_recall.policy import run_permissions
from ..models import DocumentBlock, FileDerivation, FileObject, Run
from ..storage import get_storage


def _identity(handle):
    return json.dumps(handle, sort_keys=True, separators=(",", ":"))


def _queue(frame):
    if not frame or frame.status != "active":
        return []
    pending = (frame.revalidation_state or {}).get("pending")
    if pending:
        return [dict(item) for item in pending]
    try:
        semantic = ContextSummary.model_validate_json(frame.summary)
    except ValueError:
        return []
    entries = [*semantic.constraints, *semantic.decisions, *semantic.facts, *semantic.open_items]
    if (frame.revision + 1) % 8 and not any(entry.status == "conflict" for entry in entries):
        return []
    handles = {_identity(source.model_dump(exclude_none=True)): source.model_dump(exclude_none=True) for source in semantic.sources()}
    return [{"source": handle, "offset": 0} for handle in handles.values()]


def _excerpt(db, handle, owner_id, allowed_runs, offset, limit):
    key, raw = None, None
    if handle.get("run_id"):
        run = db.get(Run, handle["run_id"])
        if not run or run.id not in allowed_runs or run.user_id != owner_id or not run_permissions(db, run)["read"]:
            return None
        raw = run.prompt + "\n" + (run.summary or "")
    if handle.get("block_id"):
        row = db.execute(
            select(DocumentBlock, FileObject)
            .select_from(DocumentBlock)
            .join(FileDerivation, DocumentBlock.derivation_id == FileDerivation.id)
            .join(FileObject, FileDerivation.file_id == FileObject.id)
            .where(DocumentBlock.id == handle["block_id"])
        ).first()
        if not row:
            return None
        block, file = row
        if file.user_id != owner_id or file.deleted_at or file.purged_at:
            return None
        key = block.text_key
    elif handle.get("file_id"):
        file = db.get(FileObject, handle["file_id"])
        if not file or file.user_id != owner_id or file.deleted_at or file.purged_at:
            return None
        meta = file.meta or {}
        key = meta.get("text_key") or (meta.get("ocr_text_key") if meta.get("ocr") == "done" else None)
    if key:
        decoder, seen, chunks = codecs.getincrementaldecoder("utf-8")("strict"), 0, []
        wanted = offset + limit + 1
        with get_storage().open_stream(key) as stream:
            for chunk in stream:
                decoded = decoder.decode(chunk)
                start = max(0, offset - seen)
                chunks.append(decoded[start : max(start, wanted - seen)])
                seen += len(decoded)
                if seen >= wanted:
                    break
            else:
                chunks.append(decoder.decode(b"", final=True))
        text = "".join(chunks)
    elif raw is not None:
        text = raw[offset : offset + limit + 1]
    else:
        return None
    return text[:limit], len(text) <= limit


def batch(db, frame, owner_id, allowed_runs):
    pending, offered, budget = _queue(frame), [], 32000
    unavailable = []
    for item in pending:
        if len(offered) == 16 or not budget:
            break
        handle, offset = item["source"], item["offset"]
        excerpt = _excerpt(db, handle, owner_id, allowed_runs, offset, min(4000, budget))
        if excerpt is None:
            unavailable.append(item)
            continue
        quote, complete = excerpt
        digest = hashlib.sha256(quote.encode()).hexdigest()
        evidence_id = hashlib.sha256((_identity(handle) + ":" + str(offset) + ":" + digest).encode()).hexdigest()
        offered.append(
            {
                **handle,
                "scope": "bounded_excerpt",
                "from_char": offset,
                "through_char": offset + len(quote),
                "sha256": digest,
                "text": quote,
                "source_complete": complete,
                "evidence_id": evidence_id,
            }
        )
        budget -= len(quote)
    return offered, [item for item in pending if item not in unavailable]


def evidence(db, frame, owner_id, allowed_runs):
    return batch(db, frame, owner_id, allowed_runs)[0]


def advance(db, frame, owner_id, allowed_runs, consumed_ids):
    offered, pending = batch(db, frame, owner_id, allowed_runs)
    if consumed_ids != [item["evidence_id"] for item in offered[: len(consumed_ids)]]:
        raise ValueError("revalidation cursor must acknowledge the exact offered prefix")
    for item in offered[: len(consumed_ids)]:
        handle = {key: item[key] for key in ("run_id", "file_id", "block_id") if key in item}
        match = next(candidate for candidate in pending if candidate["source"] == handle)
        if item["source_complete"]:
            pending.remove(match)
        else:
            match["offset"] = item["through_char"]
    return {"pending": pending}
