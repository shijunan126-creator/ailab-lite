"""Deletion lineage: when a conversation goes, so does what the memory system kept *of* it.

Architecture doc §13.3 asks that deleting evidence propagate to everything derived from it. We
split "derived" in two:

* **Verbatim copies** of the conversation (episode digests, source quotes, evidence links) are
  removed, because they are the conversation under another name.
* **Facts** the user may have come to rely on stay, like saved memories in ChatGPT. They are
  flagged ``evidence_deleted`` so the memory page can say so, and the user can delete them there.

``FORGET_DERIVED_FACTS`` flips the second rule for deployments that want the strict reading.
"""

from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from ..audit import record_audit
from ..models import MemoryDreamRun, MemoryEpisode, MemoryWorkItem, Run, RunDelivery, User, utcnow
from . import embeddings
from .tombstones import fact_hash

FORGET_DERIVED_FACTS = False  # True: also drop facts whose only evidence was the deleted conversation


def forget_conversation(db: Session, user: User, conversation_id: str) -> dict:
    return forget_conversations(db, user, [conversation_id])


def forget_conversations(db: Session, user: User, conversation_ids: list[str]) -> dict:
    """Caller holds the user's critical section. Does not commit. One pass and one revision for any number of conversations."""
    if not conversation_ids:
        return {"episodes": 0, "facts": 0, "dropped": 0}
    from ..skill_drafts.service import invalidate_drafts

    invalidate_drafts(db, user.id, conversation_ids)
    from .search import _doc

    _doc.cache_clear()
    run_ids = set(db.scalars(select(Run.id).where(Run.conversation_id.in_(conversation_ids), Run.user_id == user.id)))
    embeddings.delete_for_runs(db, list(run_ids))
    db.execute(delete(MemoryWorkItem).where(MemoryWorkItem.run_id.in_(run_ids)))
    match = [MemoryEpisode.conversation_id.in_(conversation_ids)]
    if run_ids:
        match.append(MemoryEpisode.run_id.in_(run_ids))
    episode_ids = set(db.scalars(select(MemoryEpisode.id).where(MemoryEpisode.user_id == user.id, or_(*match))))
    if episode_ids:
        db.execute(delete(MemoryEpisode).where(MemoryEpisode.id.in_(episode_ids)))

    def redact(entry):
        if set(entry.get("evidence_run_ids") or []) & run_ids or set(entry.get("evidence_episode_ids") or []) & episode_ids:
            return {
                **entry,
                "evidence_run_ids": [r for r in entry.get("evidence_run_ids", []) if r not in run_ids],
                "evidence_episode_ids": [e for e in entry.get("evidence_episode_ids", []) if e not in episode_ids],
                "source_quote": "",
                "evidence_deleted": True,
            }
        return entry

    _rewrite_copies(db, user.id, redact)
    for dream in db.scalars(select(MemoryDreamRun).where(MemoryDreamRun.user_id == user.id).execution_options(yield_per=100)):
        if set(dream.input_run_ids or []) & run_ids:
            dream.report = {**(dream.report or {}), "candidates": []}

    stored = (user.settings or {}).get("memory") or {}
    entries, changed, dropped = [], 0, 0
    for entry in stored.get("entries") or []:
        runs = set(entry.get("evidence_run_ids") or [])
        eps = set(entry.get("evidence_episode_ids") or [])
        if not (runs & run_ids or eps & episode_ids):
            entries.append(entry)
            continue
        remaining_runs = [r for r in entry.get("evidence_run_ids") or [] if r not in run_ids]
        if FORGET_DERIVED_FACTS and not remaining_runs and entry.get("source") != "user":
            dropped += 1
            continue
        entries.append(
            {
                **entry,
                "evidence_run_ids": remaining_runs,
                "evidence_episode_ids": [e for e in entry.get("evidence_episode_ids") or [] if e not in episode_ids],
                "source_quote": "",
                "evidence_deleted": True,
            }
        )
        changed += 1
    if changed or dropped:
        revision = int(stored.get("revision", 0)) + 1  # an open memory page must reload before it saves
        user.settings = {**(user.settings or {}), "memory": {**stored, "revision": revision, "entries": entries}}
        db.add(user)
        record_audit(
            db,
            "memory.evidence_deleted",
            "user",
            user.id,
            actor_id=user.id,
            detail={"facts": changed, "dropped": dropped, "episodes": len(episode_ids), "at": utcnow().isoformat()},
        )
    return {"episodes": len(episode_ids), "facts": changed, "dropped": dropped}


def purge_episodes_for_runs(db: Session, run_ids: list[str]) -> int:
    """Backstop for the hard purge of a conversation: no episode may outlive its run."""
    if not run_ids:
        return 0
    from .search import _doc

    _doc.cache_clear()
    embeddings.delete_for_runs(db, run_ids)
    db.execute(delete(MemoryWorkItem).where(MemoryWorkItem.run_id.in_(run_ids)))
    result = db.execute(delete(MemoryEpisode).where(MemoryEpisode.run_id.in_(run_ids)))
    return result.rowcount or 0


def _rewrite_copies(db: Session, user_id: str, rewrite) -> None:
    """Deletion is synchronous for every authoritative copy, including retry and undo payloads."""

    def document(value):
        return {**value, "entries": [next_ for entry in value.get("entries", []) if (next_ := rewrite(entry)) is not None]}

    for dream in db.scalars(select(MemoryDreamRun).where(MemoryDreamRun.user_id == user_id).execution_options(yield_per=100)):
        for field in ("before_memory", "after_memory"):
            value = getattr(dream, field)
            if value and (updated := document(value)) != value:
                setattr(dream, field, updated)
        candidates = []
        for entry in (dream.report or {}).get("candidates", []):
            next_ = rewrite(entry)
            if next_ is not None:
                candidates.append(
                    {**next_, "related": [item for related in next_.get("related", []) if (item := rewrite(related)) is not None]}
                )
        if candidates != (dream.report or {}).get("candidates", []):
            dream.report = {**(dream.report or {}), "candidates": candidates}
        changes = [next_ for entry in (dream.plan or {}).get("changes", []) if (next_ := rewrite(entry)) is not None]
        if changes != (dream.plan or {}).get("changes", []):
            dream.plan = {**(dream.plan or {}), "changes": changes}
    for item in db.scalars(
        select(MemoryWorkItem).where(MemoryWorkItem.user_id == user_id, MemoryWorkItem.state == "pending").execution_options(yield_per=100)
    ):
        updated = [next_ for entry in item.candidates or [] if (next_ := rewrite(entry)) is not None]
        if updated != (item.candidates or []):
            item.candidates = updated
    for delivery in db.scalars(
        select(RunDelivery).join(Run, Run.id == RunDelivery.run_id).where(Run.user_id == user_id).execution_options(yield_per=100)
    ):
        if isinstance(delivery.result, dict) and "entries" in delivery.result:
            updated = document(delivery.result)
            if updated != delivery.result:
                delivery.result = updated


def redact_memory_copies(db: Session, user_id: str, removed: list[dict]) -> None:
    ids = {e["id"] for e in removed}
    hashes = {(e.get("project_id"), fact_hash(e["content"])) for e in removed}

    def rewrite(entry):
        if entry.get("id") in ids or (entry.get("project_id"), fact_hash(entry.get("content", entry.get("text", "")))) in hashes:
            return None
        return entry

    _rewrite_copies(db, user_id, rewrite)
    # Derived human summaries and undo text may contain a removed fact verbatim. Do not
    # retain them as an alternate read/restore route around the deletion fence.
    from ..models import MemorySummary

    scopes = {e.get("project_id") for e in removed}
    for row in db.scalars(select(MemorySummary).where(MemorySummary.user_id == user_id)):
        if row.project_id in scopes:
            row.text = ""
            row.source_digest = ""
    for dream in db.scalars(select(MemoryDreamRun).where(MemoryDreamRun.user_id == user_id).execution_options(yield_per=100)):
        report = dict(dream.report or {})
        report.pop("summary_before", None)
        report.pop("reply", None)
        if report != (dream.report or {}):
            dream.report = report
        if dream.instruction and not (dream.trigger == "edit" and dream.status in {"pending", "running"}):
            dream.instruction = ""


def clear_memory_scope_copies(db: Session, user_id: str, project_id: str | None) -> None:
    """Clear the whole scope, including old snapshots and explicit edits queued before an empty clear."""
    _rewrite_copies(db, user_id, lambda entry: None if entry.get("project_id") == project_id else entry)
    for dream in db.scalars(
        select(MemoryDreamRun)
        .where(MemoryDreamRun.user_id == user_id, MemoryDreamRun.project_id == project_id)
        .execution_options(yield_per=100)
    ):
        report = dict(dream.report or {})
        report.pop("summary_before", None)
        report.pop("reply", None)
        dream.report = report
        dream.instruction = ""
    cancel_memory_edits(db, user_id, project_id, "记忆已清空或项目已删除，本次修改已取消")


def cancel_memory_edits(db: Session, user_id: str, project_id: str | None, reason: str) -> None:
    """A scope boundary fences old explicit instructions even if the user later switches back."""
    for dream in db.scalars(
        select(MemoryDreamRun).where(
            MemoryDreamRun.user_id == user_id,
            MemoryDreamRun.project_id == project_id,
            MemoryDreamRun.trigger == "edit",
            MemoryDreamRun.status.in_(("pending", "running")),
        )
    ):
        dream.status = "failed"
        dream.error = reason
        dream.claim_token = None
        dream.claimed_until = None
        dream.finished_at = utcnow()
