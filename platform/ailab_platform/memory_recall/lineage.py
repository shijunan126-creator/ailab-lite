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
        dream.before_memory = document(dream.before_memory or {})
        dream.after_memory = document(dream.after_memory or {})
        candidates = []
        for entry in (dream.report or {}).get("candidates", []):
            next_ = rewrite(entry)
            if next_ is not None:
                candidates.append(
                    {**next_, "related": [item for related in next_.get("related", []) if (item := rewrite(related)) is not None]}
                )
        dream.report = {**(dream.report or {}), "candidates": candidates}
        dream.plan = {
            **(dream.plan or {}),
            "changes": [next_ for entry in (dream.plan or {}).get("changes", []) if (next_ := rewrite(entry)) is not None],
        }
    for item in db.scalars(
        select(MemoryWorkItem).where(MemoryWorkItem.user_id == user_id, MemoryWorkItem.state == "pending").execution_options(yield_per=100)
    ):
        item.candidates = [next_ for entry in item.candidates or [] if (next_ := rewrite(entry)) is not None]
    for delivery in db.scalars(
        select(RunDelivery).join(Run, Run.id == RunDelivery.run_id).where(Run.user_id == user_id).execution_options(yield_per=100)
    ):
        if isinstance(delivery.result, dict) and "entries" in delivery.result:
            delivery.result = document(delivery.result)


def redact_memory_copies(db: Session, user_id: str, removed: list[dict]) -> None:
    ids = {e["id"] for e in removed}
    hashes = {fact_hash(e["content"]) for e in removed}

    def rewrite(entry):
        if entry.get("id") in ids or fact_hash(entry.get("content", entry.get("text", ""))) in hashes:
            return None
        return entry

    _rewrite_copies(db, user_id, rewrite)
