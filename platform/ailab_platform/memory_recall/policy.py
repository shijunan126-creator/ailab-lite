"""One visibility and context policy for every model-facing memory path."""

from __future__ import annotations

from datetime import UTC, datetime

from . import text

CONTEXT_CHARS = 4000
CONTEXT_FACTS = 20


def visible(entry: dict, project_id: str | None = None, mode: str = "shared", *, as_of: datetime | None = None) -> bool:
    if entry.get("suppressed") or entry.get("status", "active") not in ({"active", "closed"} if as_of else {"active"}):
        return False
    scope = entry.get("project_id")
    if scope != project_id and (scope is not None or (project_id is not None and mode == "isolated")):
        return False
    if as_of is None:
        return (
            not entry.get("superseded_by")
            and not entry.get("invalid_at")
            and (not entry.get("expires_at") or datetime.fromisoformat(entry["expires_at"]).replace(tzinfo=UTC) > datetime.now(UTC))
        )
    instant = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
    for key, before in (("valid_from", True), ("invalid_at", False), ("expires_at", False)):
        if entry.get(key):
            at = datetime.fromisoformat(entry[key])
            at = at.replace(tzinfo=UTC) if at.tzinfo is None else at.astimezone(UTC)
            if (before and instant < at) or (not before and instant >= at):
                return False
    return True


def model_view(
    document: dict, project_id: str | None = None, *, mode: str = "shared", query: str | None = None, as_of: datetime | None = None
) -> dict:
    entries = (
        []
        if not document.get("enabled", True) or not document.get("read_enabled", True)
        else [e for e in document.get("entries", []) if visible(e, project_id, mode=mode, as_of=as_of)]
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
    return {
        **document,
        "entries": entries,
        "entity_catalog": [
            e
            for e in document.get("entity_catalog", [])
            if e.get("project_id") == project_id or (e.get("project_id") is None and mode != "isolated")
        ],
    }


def run_project(db, run) -> str | None:
    from ..models import Conversation

    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    return conversation.project_id if conversation and conversation.deleted_at is None else None


def run_permissions(db, run) -> dict:
    from ..models import Conversation

    conv = db.get(Conversation, run.conversation_id, populate_existing=True) if run.conversation_id else None
    if not source_available(db, run):
        return {"read": False, "write": False}
    current = (conv.memory_policy or {}) if conv else {}
    frozen = (run.options or {}).get("memory_policy") or {}
    return {key: current.get(key, True) and frozen.get(key, True) for key in ("read", "write")}


def review_required(content: str) -> bool:
    """Sensitive personal inferences need a human decision, rather than an invented confidence threshold."""
    import re

    return bool(
        re.search(r"诊断|患有|病史|宗教信仰|政治倾向|性取向|身份证|银行卡|diagnosed|medical condition|sexual orientation", content, re.I)
    )


def source_available(db, run) -> bool:
    from sqlalchemy import select

    from ..api.source_lifecycle import invalid_sources_query
    from ..models import Run, User
    from ..run_visibility import conversation_available

    if not conversation_available(db, run):
        return False
    owner = db.get(User, run.user_id, populate_existing=True)
    if owner is None or not owner.context_source_epoch:
        return owner is not None
    return db.scalar(select(Run.id).where(Run.id == run.id, Run.id.in_(invalid_sources_query(run.user_id)))) is None


def project_mode(db, project_id: str | None) -> str:
    from ..models import Project

    project = db.get(Project, project_id, populate_existing=True) if project_id else None
    return project.memory_mode if project else "shared"


def history_pool(db, user_id, project_id):
    from sqlalchemy import or_, select

    from ..models import Conversation, Project

    if project_id and project_mode(db, project_id) == "isolated":
        return Conversation.project_id == project_id
    shared = select(Project.id).where(Project.user_id == user_id, Project.memory_mode == "shared")
    return or_(Conversation.project_id.is_(None), Conversation.project_id.in_(shared))


def history_pool_reader(db, current):
    """Freeze project modes once for a direct recall request, including its neighbouring turns."""
    from sqlalchemy import select

    from ..models import Project

    isolated = set(db.scalars(select(Project.id).where(Project.user_id == current.user_id, Project.memory_mode == "isolated")))
    left = run_project(db, current)

    def allows(source):
        right = run_project(db, source)
        return left == right if left in isolated else right not in isolated

    return allows


def same_history_pool(db, current, source):
    left, right = run_project(db, current), run_project(db, source)
    if left and project_mode(db, left) == "isolated":
        return left == right
    return not right or project_mode(db, right) == "shared"


def priority(kind, origin):
    return 0 if kind in {"user", "preference"} and origin != "inferred" else 2 if origin == "inferred" else 3 if kind == "project" else 1


def core_catalog(memory):
    import math
    import re
    from collections import defaultdict
    from html import unescape

    from .guard import reject_reason
    from .store import stamp

    entries = [
        e
        for e in memory.get("entries", [])
        if not e.get("suppressed")
        and not e.get("invalid_at")
        and not e.get("superseded_by")
        and e.get("status", "active") == "active"
        and (not e.get("expires_at") or stamp(e["expires_at"]) > datetime.now(UTC))
    ]
    core = sorted(
        (e for e in entries if e.get("tier", "core") == "core"),
        key=lambda e: (
            priority(e.get("kind", "memory"), e.get("origin")),
            -stamp(e.get("reinforced_at"), datetime.min.replace(tzinfo=UTC)).timestamp(),
            e["id"],
        ),
    )
    chosen, used = [], 0
    for entry in core:
        cost = len(entry["content"]) + 16
        if used + cost <= 1500:
            chosen.append(entry)
            used += cost
    chosen.sort(key=lambda e: (e.get("page", ""), priority(e.get("kind"), e.get("origin")), e["id"]))
    pages = defaultdict(int)
    attributed = set()
    active_names = set()
    for e in entries:
        pages[e.get("page", "工作与身份")] += 1
        active_names.update(e.get("entities", []))
        if e.get("source_quote") or e.get("origin") in {"explicit", "manual", "chat"}:
            attributed.update(e.get("entities", []))
    # A fixed reference day makes repeated rendering within the day byte-stable.
    today = datetime.now(UTC).date()
    entities = memory.get("entity_catalog", [])
    scores = {}
    for e in entities:
        name = e["canonical"]
        if name in active_names and (name in attributed or e["conversations"] >= 2):
            age = max(0, (today - stamp(e["last_seen_at"]).date()).days)
            scores[name] = max(scores.get(name, 0), max(1, e["conversations"]) * math.exp(-math.log(2) * age / 30))
    if not entities:
        scores = {name: 1 for name in attributed}
    names = [*sorted(pages, key=lambda p: (-pages[p], p)), *sorted(scores, key=lambda n: (-scores[n], n))]
    clean = []
    used = 0
    for name in names:
        name = re.sub(r"[\x00-\x1f`*_#\[\]]", "", re.sub(r"<[^>]*>", "", unescape(str(name)))).strip()
        if not name or len(name) > 20 or reject_reason(name) or name in clean:
            continue
        cost = len(name) + bool(clean)
        if used + cost > 300:
            break
        clean.append(name)
        used += cost
    return chosen, "、".join(clean)
