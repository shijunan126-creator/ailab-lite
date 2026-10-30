"""Online source fences plus durable, bounded cleanup of derived run state."""

from sqlalchemy import delete, or_, select, tuple_, update

from ..models import (
    ContextSummaryFrame,
    ContextSummarySource,
    Conversation,
    FileObject,
    FileSourceInvalidation,
    MemoryDreamRun,
    MessageFileRef,
    Run,
    RunCheckpoint,
    RunDelivery,
    RunEvent,
    RunStatus,
    User,
)
from ..run_state import transition_run


def descendants(user_id, file_ids=None):
    query = select(Run.id, MessageFileRef.invalidated_epoch.label("epoch")).join(MessageFileRef, MessageFileRef.run_id == Run.id)
    query = query.where(Run.user_id == user_id, MessageFileRef.invalidated_epoch.is_not(None))
    if file_ids is not None:
        query = query.where(MessageFileRef.file_id.in_(file_ids))
    else:
        query = query.join(FileSourceInvalidation, FileSourceInvalidation.file_id == MessageFileRef.file_id).where(
            FileSourceInvalidation.user_id == user_id
        )
    roots = query.distinct().cte("source_descendants", recursive=True)
    return roots.union(
        select(Run.id, roots.c.epoch)
        .join(roots, Run.parent_run_id == roots.c.id)
        .where(Run.user_id == user_id, Run.context_source_epoch < roots.c.epoch)
    )


def invalid_history(db, ids):
    roots = {}
    for rid, epoch, fallback in db.execute(
        select(MessageFileRef.run_id, MessageFileRef.invalidated_epoch, User.context_source_epoch)
        .outerjoin(FileObject, FileObject.id == MessageFileRef.file_id)
        .join(Run, Run.id == MessageFileRef.run_id)
        .join(User, User.id == Run.user_id)
        .where(
            MessageFileRef.run_id.in_(ids),
            or_(
                MessageFileRef.invalidated_epoch.is_not(None),
                FileObject.id.is_(None),
                FileObject.deleted_at.is_not(None),
                FileObject.purged_at.is_not(None),
            ),
        )
    ):
        roots.setdefault(rid, set()).add(epoch if epoch is not None else fallback)
    epochs = dict(db.execute(select(Run.id, Run.context_source_epoch).where(Run.id.in_(ids))).all())
    invalid = set()
    active = set()
    for rid in ids:
        active.update(roots.get(rid, set()))
        active = {epoch for epoch in active if epoch > epochs.get(rid, 0)}
        if active:
            invalid.add(rid)
    return invalid


def invalidate(db, user_id, file_ids):
    if not file_ids:
        return
    db.execute(update(User).where(User.id == user_id).values(context_source_epoch=User.context_source_epoch + 1))
    epoch = db.scalar(select(User.context_source_epoch).where(User.id == user_id))
    for file in db.scalars(select(FileObject).where(FileObject.id.in_(file_ids))):
        meta = dict(file.meta or {})
        if meta.get("ocr") in {"pending", "running"}:
            meta.update(ocr="canceled", ocr_reason="source_unavailable", ocr_generation="canceled")
        if (meta.get("preview") or {}).get("status") == "pending":
            meta["preview"] = {"status": "failed", "reason": "source_unavailable"}
            meta.pop("preview_job_id", None)
        if meta.get("summary"):
            meta["summary"] = {"status": "invalidated", "text": ""}
        file.meta = meta
    db.execute(
        update(MessageFileRef)
        .where(MessageFileRef.file_id.in_(file_ids), MessageFileRef.invalidated_epoch.is_(None))
        .values(invalidated_epoch=epoch)
    )
    from ..skill_drafts.service import invalidate_drafts

    affected = descendants(user_id, file_ids)
    conversation_ids = list(
        db.scalars(select(Run.conversation_id).where(Run.id.in_(select(affected.c.id)), Run.conversation_id.is_not(None)).distinct())
    )
    if conversation_ids:
        invalidate_drafts(db, user_id, conversation_ids)
    for fid in file_ids:
        item = db.get(FileSourceInvalidation, fid)
        if item:
            item.state, item.cursor = "pending", ""
        else:
            db.add(FileSourceInvalidation(file_id=fid, user_id=user_id, state="pending", cursor=""))
    path = descendants(user_id, file_ids)
    # Admission already bounds active runs per user. Freeze them in the deletion transaction.
    active = db.scalars(select(Run).where(Run.id.in_(select(path.c.id)), Run.status.in_(RunStatus.ACTIVE))).all()
    for run in active:
        if run.conversation_id:
            db.execute(update(Conversation).where(Conversation.id == run.conversation_id).values(queue_paused=True))
        run.runtime_meta = {**(run.runtime_meta or {}), "source_invalidated": True}
        transition_run(db, run.id, RunStatus.CANCELED, reason="file_source_unavailable", commit=False)


def drain_invalidations(db, limit=100):
    from ..library import schedule_object_deletion

    item = db.scalar(
        select(FileSourceInvalidation)
        .where(FileSourceInvalidation.state.in_(("pending", "events", "dreams", "deliveries")))
        .order_by(FileSourceInvalidation.file_id)
        .limit(1)
    )
    if not item:
        return 0
    path = descendants(item.user_id, [item.file_id])
    if item.state == "events":
        rows = db.scalars(
            select(RunEvent)
            .where(RunEvent.run_id.in_(select(path.c.id)), RunEvent.id > int(item.cursor or 0))
            .order_by(RunEvent.id)
            .limit(limit)
        ).all()
        for row in rows:
            row.payload = {"source_unavailable": True}
        if rows:
            item.cursor = str(rows[-1].id)
        else:
            item.state, item.cursor = "dreams", ""
        db.commit()
        return len(rows)
    if item.state != "pending":
        return _drain_memory_copies(db, item, path, limit)
    runs = db.scalars(select(Run).where(Run.id.in_(select(path.c.id)), Run.id > item.cursor).order_by(Run.id).limit(limit)).all()
    from ..memory_recall.lineage import purge_episodes_for_runs

    if runs:
        purge_episodes_for_runs(db, [run.id for run in runs])
        owner = db.get(User, item.user_id, populate_existing=True)
        stored = (owner.settings or {}).get("memory") or {}
        removed_ids = {run.id for run in runs}
        entries = []
        changed = False
        for entry in stored.get("entries") or []:
            if removed_ids.intersection(entry.get("evidence_run_ids") or []):
                changed = True
                if entry.get("source") != "user":
                    continue
                entry = {
                    **entry,
                    "source_quote": "",
                    "evidence_deleted": True,
                    "evidence_run_ids": [rid for rid in entry.get("evidence_run_ids") or [] if rid not in removed_ids],
                }
            entries.append(entry)
        if changed:
            owner.settings = {**(owner.settings or {}), "memory": {**stored, "entries": entries, "revision": stored.get("revision", 0) + 1}}
    for run in runs:
        meta = dict(run.runtime_meta or {})
        meta["source_invalidated"] = True
        workspace = meta.pop("workspace", None)
        if workspace and workspace.get("key"):
            schedule_object_deletion(db, workspace["key"], "file_source")
        run.runtime_meta = meta
        result = dict(run.result or {})
        if result.get("workspace_key"):
            schedule_object_deletion(db, result["workspace_key"], "file_source")
        result.pop("workspace_key", None)
        result["workspace_saved"] = False
        run.summary = "（文件来源已失效）"
        run.result = {
            key: value for key, value in result.items() if key in {"artifacts", "forked_usage", "workspace_saved", "forked_from_run_id"}
        }
        schedule_object_deletion(db, f"previews/{run.id}/", "file_source", prefix=True)
        schedule_object_deletion(db, f"workspaces/{run.id}.tgz", "file_source")
        schedule_object_deletion(db, f"workspaces/{run.id}/", "file_source", prefix=True)
        for key in db.scalars(select(RunCheckpoint.state_key).where(RunCheckpoint.run_id == run.id)):
            schedule_object_deletion(db, key, "file_source")
        db.execute(delete(RunCheckpoint).where(RunCheckpoint.run_id == run.id))
        if run.conversation_id:
            db.execute(
                update(Conversation)
                .where(Conversation.id == run.conversation_id)
                .values(summary=None, summary_through_run_id=None, summary_frame_id=None)
            )
    frames = select(ContextSummarySource.frame_id).where(ContextSummarySource.file_id == item.file_id)
    # At most one frame page per tick. Online assembly rejects affected frames immediately.
    stale = list(db.scalars(frames.limit(limit)))
    if stale:
        db.execute(update(ContextSummaryFrame).where(ContextSummaryFrame.id.in_(stale)).values(summary="", status="invalidated"))
        db.execute(delete(ContextSummarySource).where(ContextSummarySource.frame_id.in_(stale)))
    if runs:
        item.cursor = runs[-1].id
    elif not stale:
        item.state, item.cursor = "events", ""
    db.commit()
    return len(runs) + len(stale)


def invalid_sources_query(user_id):
    path = descendants(user_id)
    return select(path.c.id)


def _drain_memory_copies(db, item, path, limit):
    # Retry/undo snapshots and replay payloads are authoritative copies too. Page them separately.
    def redact(entry):
        ids = entry.get("evidence_run_ids") or []
        invalid = set(db.scalars(select(Run.id).where(Run.id.in_(ids), Run.id.in_(select(path.c.id))))) if ids else set()
        if not invalid:
            return entry
        if entry.get("source") != "user":
            return None
        return {
            **entry,
            "source_quote": "",
            "evidence_deleted": True,
            "evidence_run_ids": [rid for rid in ids if rid not in invalid],
            "evidence_episode_ids": [],
        }

    def document(value):
        return {**value, "entries": [clean for entry in value.get("entries", []) if (clean := redact(entry)) is not None]}

    if item.state == "dreams":
        rows = db.scalars(
            select(MemoryDreamRun)
            .where(MemoryDreamRun.user_id == item.user_id, MemoryDreamRun.id > item.cursor)
            .order_by(MemoryDreamRun.id)
            .limit(limit)
        ).all()
        for row in rows:
            row.before_memory = document(row.before_memory or {})
            row.after_memory = document(row.after_memory or {})
            if row.input_run_ids and db.scalar(select(Run.id).where(Run.id.in_(row.input_run_ids), Run.id.in_(select(path.c.id))).limit(1)):
                row.report = {**(row.report or {}), "candidates": []}
            row.plan = {
                **(row.plan or {}),
                "changes": [clean for entry in (row.plan or {}).get("changes", []) if (clean := redact(entry)) is not None],
            }
        if not rows:
            item.state, item.cursor = "deliveries", ""
    else:
        after_run, _, after_delivery = item.cursor.partition("|")
        rows = db.scalars(
            select(RunDelivery)
            .join(Run, Run.id == RunDelivery.run_id)
            .where(Run.user_id == item.user_id, tuple_(RunDelivery.run_id, RunDelivery.delivery_id) > (after_run, after_delivery))
            .order_by(RunDelivery.run_id, RunDelivery.delivery_id)
            .limit(limit)
        ).all()
        for row in rows:
            if db.scalar(select(path.c.id).where(path.c.id == row.run_id).limit(1)):
                row.result = {"source_unavailable": True}
            elif isinstance(row.result, dict) and "entries" in row.result:
                row.result = document(row.result)
        if not rows:
            item.state, item.cursor = "done", ""
    if rows:
        item.cursor = rows[-1].id if item.state == "dreams" else rows[-1].run_id + "|" + rows[-1].delivery_id
    db.commit()
    return len(rows)


def visible_memory_entries(db, user_id, entries):
    ids = {rid for entry in entries for rid in entry.get("evidence_run_ids") or []}
    if not ids:
        return entries
    invalid = set(db.scalars(select(Run.id).where(Run.id.in_(ids), Run.id.in_(invalid_sources_query(user_id)))))
    result = []
    for entry in entries:
        if not invalid.intersection(entry.get("evidence_run_ids") or []):
            result.append(entry)
        elif entry.get("source") == "user":
            result.append(
                {
                    **entry,
                    "source_quote": "",
                    "evidence_deleted": True,
                    "evidence_run_ids": [rid for rid in entry.get("evidence_run_ids") or [] if rid not in invalid],
                    "evidence_episode_ids": [],
                }
            )
    return result
