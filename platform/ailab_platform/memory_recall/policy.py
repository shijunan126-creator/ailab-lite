"""One visibility and context policy for every model-facing memory path."""

from __future__ import annotations

from datetime import UTC, datetime

from . import text

CONTEXT_CHARS = 4000
CONTEXT_FACTS = 20


def visible(entry: dict, project_id: str | None = None, *, project_only: bool = False, as_of: datetime | None = None) -> bool:
    if entry.get("suppressed") or entry.get("status", "active") != "active":
        return False
    scope = entry.get("project_id")
    if scope != project_id and (scope is not None or project_only):
        return False
    if as_of is None:
        return not entry.get("superseded_by") and not entry.get("invalid_at")
    instant = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
    for key, before in (("valid_from", True), ("invalid_at", False)):
        if entry.get(key):
            at = datetime.fromisoformat(entry[key])
            at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
            if (before and instant < at) or (not before and instant >= at):
                return False
    return True


def model_view(document: dict, project_id: str | None = None, *, query: str | None = None, as_of: datetime | None = None) -> dict:
    entries = (
        []
        if not document.get("enabled", True) or not document.get("read_enabled", True)
        else [
            e
            for e in document.get("entries", [])
            if visible(e, project_id, project_only=bool(project_id and document.get("project_only")), as_of=as_of)
        ]
    )
    if query is not None:
        docs = [text.build_doc(e["id"], (e["content"], 1.0)) for e in entries]
        scores = {h.key: h.score for h in text.search(text.query_tokens(query), docs) if h.coverage >= 0.2}
        ranked = sorted(entries, key=lambda e: (-scores.get(e["id"], 0), e["id"]))
        chosen, used = [], 0
        for entry in ranked:
            if entry["id"] not in scores and entry.get("kind") != "user":
                continue
            cost = len(entry["content"]) + 160
            if used + cost > CONTEXT_CHARS or len(chosen) == CONTEXT_FACTS:
                continue
            chosen.append(entry)
            used += cost
        entries = chosen
    return {**document, "entries": entries}


def run_project(db, run) -> str | None:
    from ..models import Conversation

    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    return conversation.project_id if conversation and conversation.deleted_at is None else None


def run_permissions(db, run) -> dict:
    from ..models import Conversation

    conv = db.get(Conversation, run.conversation_id, populate_existing=True) if run.conversation_id else None
    current = (conv.memory_policy or {}) if conv else {}
    frozen = (run.options or {}).get("memory_policy") or {}
    return {key: current.get(key, True) and frozen.get(key, True) for key in ("read", "write")}


def review_required(content: str) -> bool:
    """Sensitive personal inferences need a human decision, rather than an invented confidence threshold."""
    import re

    return bool(
        re.search(r"诊断|患有|病史|宗教信仰|政治倾向|性取向|身份证|银行卡|diagnosed|medical condition|sexual orientation", content, re.I)
    )
