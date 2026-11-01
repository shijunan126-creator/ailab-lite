"""Copy branch-local identities while retaining shared immutable file handles."""

import hashlib
from copy import deepcopy

from sqlalchemy import select

from ..contracts.context_summary import ContextSummary
from ..models import AppliedMessagePosition, ContextSummaryFrame, ContextSummarySource, MessageFileRef, QueuedMessage, new_id


def copy_message_sources(db, old, new, steers):
    from .document_sources import bind_refs

    identities = {old.id: new.id, **steers}
    refs = db.scalars(select(MessageFileRef).where(MessageFileRef.run_id == old.id)).all()
    for ref in refs:
        message_id = new_id("art") if ref.message_kind == "artifact" else identities.get(ref.message_id)
        if message_id is None:
            continue
        db.add(
            MessageFileRef(
                run_id=new.id,
                message_kind=ref.message_kind,
                message_id=message_id,
                file_id=ref.file_id,
                role=ref.role,
                ordinal=ref.ordinal,
                reference_origin=ref.reference_origin,
                filename_snapshot=ref.filename_snapshot,
                upload_id=ref.upload_id,
                invalidated_epoch=ref.invalidated_epoch,
                created_at=ref.created_at,
            )
        )
    # Legacy runs may predate either relation table. Fill only their missing prompt references.
    db.flush()
    bind_refs(db, "run_prompt", new.id, new.input_file_ids, new.id)
    for qid in steers.values():
        queued = db.get(QueuedMessage, qid)
        bind_refs(db, "queued_message", qid, queued.file_ids, new.id)
    positions = db.scalars(select(AppliedMessagePosition).where(AppliedMessagePosition.run_id == old.id)).all()
    for position in positions:
        if position.message_id not in identities:
            continue
        db.add(
            AppliedMessagePosition(
                run_id=new.id,
                message_kind=position.message_kind,
                message_id=identities[position.message_id],
                application_seq=position.application_seq,
                alias_run_prompt_id=identities.get(position.alias_run_prompt_id),
            )
        )
    if not any(p.message_kind == "run_prompt" for p in positions):
        db.add(AppliedMessagePosition(run_id=new.id, message_kind="run_prompt", message_id=new.id, application_seq=0))
    positioned = {p.message_id for p in positions if p.message_kind == "queued_message"}
    next_seq = max((p.application_seq for p in positions), default=0)
    for old_qid, qid in steers.items():
        if old_qid not in positioned:
            next_seq += 1
            db.add(AppliedMessagePosition(run_id=new.id, message_kind="queued_message", message_id=qid, application_seq=next_seq))


def copy_summary(db, source, target, mapping, invalid):
    through = source.summary_through_run_id
    if through not in mapping or through in invalid:
        return
    previous = db.get(ContextSummaryFrame, source.summary_frame_id) if source.summary_frame_id else None
    if previous and previous.status != "active":
        return
    summary = source.summary
    try:
        semantic = ContextSummary.model_validate_json(summary)
    except ValueError:
        if previous:
            return  # A malformed structured frame must be rebuilt from the retained original turns.
    else:
        for handle in semantic.sources():
            if handle.run_id:
                if handle.run_id not in mapping or handle.run_id in invalid:
                    return
                handle.run_id = mapping[handle.run_id].id
        summary = semantic.model_dump_json()
    target.summary = summary
    target.summary_through_run_id = mapping[through].id
    if not previous:
        return
    pending = deepcopy(previous.revalidation_state or {})
    for item in pending.get("pending", []):
        handle = item["source"]
        if handle.get("run_id"):
            if handle["run_id"] not in mapping or handle["run_id"] in invalid:
                target.summary, target.summary_through_run_id = "", None
                return
            handle["run_id"] = mapping[handle["run_id"]].id
    frame = ContextSummaryFrame(
        conversation_id=target.id,
        branch_anchor=mapping.get(previous.branch_anchor, mapping[through]).id,
        revision=previous.revision,
        from_run_id=mapping[previous.from_run_id].id if previous.from_run_id in mapping else None,
        through_run_id=mapping[through].id,
        source_epoch=previous.source_epoch,
        input_digest=hashlib.sha256(summary.encode()).hexdigest(),
        revalidation_state=pending,
        summary=summary,
        status="active",
    )
    db.add(frame)
    db.flush()
    target.summary_frame_id = frame.id
    for fid in db.scalars(select(ContextSummarySource.file_id).where(ContextSummarySource.frame_id == previous.id)):
        db.add(ContextSummarySource(frame_id=frame.id, file_id=fid))
