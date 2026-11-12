"""Copy branch-local identities while retaining shared immutable file handles."""

import gzip
import hashlib
import json
import re
from copy import deepcopy

from fastapi import HTTPException
from sqlalchemy import select

from ..contracts.context_summary import ContextSummary
from ..models import AppliedMessagePosition, ContextSummaryFrame, ContextSummarySource, MessageFileRef, QueuedMessage, new_id


def copy_message_sources(db, old, new, steers):
    from .document_sources import bind_refs
    from .web_image_refs import copy_refs

    copy_refs(db, old, new)
    identities = {old.id: new.id, **steers}
    from ..models import MessageDocRef

    for ref in db.scalars(select(MessageDocRef).where(MessageDocRef.run_id == old.id)):
        if ref.message_id in identities:
            db.add(
                MessageDocRef(
                    run_id=new.id,
                    message_kind=ref.message_kind,
                    message_id=identities[ref.message_id],
                    ordinal=ref.ordinal,
                    file_id=ref.file_id,
                    target=dict(ref.target),
                    filename=ref.filename,
                )
            )
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


_RUN_HANDLE = re.compile(r"\brun_[0-9a-f]{20}\b")
# What the next run's model input reads from a parent snapshot (routes_model_context). Nothing
# else crosses: tool logs, leases, background jobs and loaded skills belong to the source run.
_INHERITED = ("context_abi_version", "context_epoch", "prompt_anchors", "delivered", "delivery_names", "plan", "workspace_dirty")
_MEMORY = ("memory_observed", "memory_snapshot_signature")


def fork_checkpoint(db, target, runs, captures, *, mode, workspace, invalid):
    """The fork's own copy of the exact model context it branches from, or None to rebuild.

    The copy keeps every model-visible byte, so the branch's next request extends the source's
    last request and the provider's prefix cache still applies. Structured references are moved
    to the branch's copies (captures for image restore, run handles for summary sources); run
    handles that only appear inside message text keep their source value and are recorded as
    aliases instead, because rewriting text would change the prefix.
    """
    from ..models import RunCheckpoint
    from .routes_runtime import read_checkpoint

    if (
        target.status != "succeeded"
        or target.id in invalid
        or (target.options or {}).get("mode", "work") != mode
        or not (target.runtime_meta or {}).get("context_snapshot")
        or db.get(RunCheckpoint, (target.id, target.checkpoint_step)) is None
    ):
        return None
    try:
        saved = read_checkpoint(db, target)
    except HTTPException:
        return None
    state = (saved or {}).get("state") or {}
    if not (state.get("finished") and state.get("messages") and state.get("tool_catalog_v1") is not None):
        return None
    messages = state["messages"]  # parsed for this call only; edited in place
    for message in messages:
        for part in message.get("content") if isinstance(message.get("content"), list) else []:
            capture = (part.get("file") or {}).get("capture_id") if part.get("type") == "ailab_image" else None
            if capture is not None:
                if capture not in captures:
                    return None  # Not copied (temporary or not ready): restoring it in the branch would pause.
                part["file"]["capture_id"] = captures[capture]
    sources = state.get("context_sources") or []
    for source in sources:
        if source.get("run_id") is not None:
            if source["run_id"] not in runs:
                return None
            source["run_id"] = runs[source["run_id"]]
    aliases = {old: runs[current] for old, current in (state.get("run_aliases") or {}).items() if current in runs}
    seen = set()
    _collect_handles(messages, seen)
    aliases.update({old: new for old, new in runs.items() if old in seen})
    copied = {
        "runtime_version": 1,
        "finished": True,
        "messages": messages,
        "tool_catalog_v1": state["tool_catalog_v1"],
        "context_model": state.get("context_model", target.model),
        "context_sources": sources,
        "run_aliases": aliases,
        "pending": [],
        "background_jobs": [],
        "file_wait_deadlines": {},
        # Availability is all a parent snapshot carries; the branch restores its own cloned workspace.
        "workspace": {"available": True} if workspace and state.get("workspace") else None,
        **{key: state[key] for key in (*_INHERITED, *_MEMORY) if key in state},
    }
    copied.setdefault("context_epoch", 0)
    return copied


def _collect_handles(value, seen):
    # Walk the strings instead of serializing the whole context a second time.
    if isinstance(value, str):
        if "run_" in value:
            seen.update(_RUN_HANDLE.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            _collect_handles(item, seen)
    elif isinstance(value, list):
        for item in value:
            _collect_handles(item, seen)


def store_fork_checkpoint(db, leaf, state, put):
    """Register the copy as the branch leaf's finished snapshot; it ages like any other leaf."""
    from uuid import uuid4

    from ..config import settings
    from ..models import RunCheckpoint

    raw = json.dumps(state, ensure_ascii=False).encode()
    if len(raw) > settings.checkpoint_max_bytes:
        return False
    key = f"checkpoints/{leaf.id}/1-{uuid4().hex}.json.gz"
    if not put(key, gzip.compress(raw)):
        return False
    db.add(
        RunCheckpoint(
            run_id=leaf.id,
            step=1,
            attempt_id="fork",
            state_key=key,
            plan=state.get("plan", []),
            tool_log={},
            deliveries=state.get("delivered", {}),
            runtime_version=1,
            expires_at=None,
        )
    )
    leaf.checkpoint_step = 1
    leaf.runtime_meta = {**(leaf.runtime_meta or {}), "context_snapshot": True, "context_epoch": state["context_epoch"]}
    return True


def cache_session(db, conversation):
    """The provider session for a conversation: the root of its fork lineage.

    Providers keep a prefix cache per session (OpenCode Go returns no hit for an identical prefix
    under another session), so a branch must ask under the session that cached its prefix. A purged
    ancestor still names the root: `forked_from` keeps its id.
    """
    from ..models import Conversation

    root, seen = conversation.id, {conversation.id}
    current = conversation
    while current is not None and (parent := (current.forked_from or {}).get("conversation_id")) and parent not in seen:
        if len(seen) >= 64:
            break
        root = parent
        seen.add(parent)
        current = db.get(Conversation, parent)
    return root
