"""Human-facing derived summaries; never included in the model's memory context."""

import asyncio
import hashlib
import json

from sqlalchemy import select

from ..db import SessionLocal
from ..locks import user_critical_section
from ..memory_recall.policy import model_view
from ..models import MemorySummary, Project, User, utcnow
from .prompts import SUMMARY_SYSTEM

SUMMARY_CHARS = 1200  # the prompt asks for 800; a hard stop well above it, never a cut in the middle of normal output


def _facts(document, project_id):
    """The facts a scope's summary describes: what the model may use there, whether or not memory is switched on right now
    (turning memory off must not make the summary look empty or stale)."""
    view = model_view({**document, "enabled": True, "read_enabled": True}, project_id, mode="isolated")
    return [e for e in view["entries"] if e.get("project_id") == project_id]


def scope_entries(user, project_id):
    from ..api.routes_memory import memory_for

    return _facts(memory_for(user), project_id)


def digest(entries):
    return hashlib.sha256(json.dumps(sorted((e["id"], e["content"]) for e in entries), ensure_ascii=False).encode()).hexdigest()


def summary_row(db, user_id, project_id):
    return db.scalar(select(MemorySummary).where(MemorySummary.user_id == user_id, MemorySummary.project_id == project_id))


def is_stale(row, entries):
    return row.source_digest != digest(entries) if row else bool(entries)


def stale_scopes(db, user, *, limit=None, requested=None):
    from ..api.routes_memory import memory_for

    document = memory_for(user)
    populated = {None, *(e.get("project_id") for e in document["entries"])}
    expected = {pid: digest(_facts(document, pid)) for pid in populated}
    empty = digest([])
    stale = []
    if requested and is_stale(summary_row(db, user.id, requested), _facts(document, requested)):
        stale.append(requested)
    account = summary_row(db, user.id, None)
    if is_stale(account, _facts(document, None)):
        stale.append(None)
    if limit and len(stale) >= limit:
        return stale
    # Stream only scope ids/digests: one join replaces a query and document reload
    # per project, and a bounded regeneration job can stop after its fifth miss.
    query = (
        select(Project.id, MemorySummary.source_digest)
        .outerjoin(MemorySummary, (MemorySummary.project_id == Project.id) & (MemorySummary.user_id == user.id))
        .where(Project.user_id == user.id)
        .order_by(Project.updated_at.desc(), Project.id)
        .execution_options(yield_per=100)
    )
    rows = db.execute(query)
    try:
        for pid, stored in rows:
            if pid != requested and (stored != expected.get(pid, empty) if stored is not None else expected.get(pid, empty) != empty):
                stale.append(pid)
                if limit and len(stale) >= limit:
                    break
    finally:
        rows.close()
    return stale


async def regenerate(user_id, project_id):
    from ..catalog import get_catalog
    from ..config import settings
    from .service import _chat

    def prepare():
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if not user or user.disabled_at or (project_id and not db.get(Project, project_id)):
                return
            entries = scope_entries(user, project_id)
            source = digest(entries)
            row = summary_row(db, user_id, project_id)
            if row and row.source_digest == source:
                return
            scope = f"项目「{db.get(Project, project_id).name}」" if project_id else "账号"
            return user, entries, source, scope

    snapshot = await asyncio.to_thread(prepare)
    if snapshot is None:
        return
    user, entries, source, scope = snapshot
    text = ""
    if entries:
        text, _ = await _chat(
            [
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": json.dumps({"scope": scope, "facts": [e["content"] for e in entries]}, ensure_ascii=False)},
            ],
            settings.memory_dream_model or get_catalog().default_id(),
            user,
        )
        text = text.strip()[:SUMMARY_CHARS]

    def store():
        with SessionLocal() as db:
            with user_critical_section(db, user_id):
                user = db.get(User, user_id, populate_existing=True)
                if (
                    not user
                    or user.disabled_at
                    or (project_id and not db.get(Project, project_id))
                    or digest(scope_entries(user, project_id)) != source
                ):
                    return
                row = summary_row(db, user_id, project_id)
                if row is None:
                    row = MemorySummary(user_id=user_id, project_id=project_id)
                    db.add(row)
                row.text, row.source_digest = text, source
                row.generated_at = row.updated_at = utcnow()
                db.commit()

    await asyncio.to_thread(store)
