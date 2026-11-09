"""Bounded lexical retrieval across facts and history; no automatic injection."""

from datetime import UTC, datetime
from collections import defaultdict

from . import text
from .policy import model_view
from .search import search_history, _rows


def search_memory(db, user, run, document, request, project_id, mode):
    entries = model_view(document, project_id, mode=mode, as_of=request.as_of)["entries"]
    entries = [
        e
        for e in entries
        if (not request.before or datetime.fromisoformat(e["observed_at"]).replace(tzinfo=UTC) < request.before)
        and (not request.after or datetime.fromisoformat(e["observed_at"]).replace(tzinfo=UTC) >= request.after)
    ]
    query = text.query_tokens(request.query)
    from ..models import MemoryEntity
    from sqlalchemy import select, or_

    scopes = [MemoryEntity.project_id == project_id]
    if project_id and mode == "shared":
        scopes.append(MemoryEntity.project_id.is_(None))
    for entity in db.scalars(select(MemoryEntity).where(MemoryEntity.user_id == user.id, or_(*scopes)).limit(2000)):
        if any(alias.casefold() in request.query.casefold() for alias in [entity.canonical, *entity.aliases]):
            query.extend(text.tokens(entity.canonical))
    docs = [text.build_doc(e["id"], (e["content"], 1.0)) for e in entries]
    hits = sorted((h for h in text.search(list(dict.fromkeys(query)), docs) if h.coverage >= 0.3), key=lambda h: (-h.score, h.key))
    by_id = {e["id"]: e for e in entries}
    results = []
    for rank, hit in enumerate(hits[:5]):
        e = by_id[hit.key]
        results.append(
            (
                1 / (61 + rank),
                {
                    "source": "memory",
                    "id": e["id"],
                    "content": e["content"][:120],
                    "date": e["observed_at"],
                    "run_ids": e.get("evidence_run_ids", []),
                },
            )
        )
    if document.get("reference_history", True):
        history = search_history(
            db,
            user,
            request.query,
            limit=5,
            exclude_conversation_id=run.conversation_id,
            project_id=project_id,
            before=request.before,
            after=request.after,
        )
        for rank, result in enumerate(history["results"]):
            results.append((1 / (61 + rank), {**result, "source": "history"}))
    results.sort(key=lambda pair: -pair[0])
    out = [r for _, r in results[:5]]
    return {"revision": document["revision"], "results": out, "status": "OK" if out else "NO_RELEVANT_MEMORY"}


def browse_history(db, user, run, request, project_id):
    rows = _rows(
        db, user.id, None, run.conversation_id, project_id=project_id, before=request.before, after=request.after, cursor=request.cursor
    )
    selected = rows[: request.limit]
    return {
        "results": [
            {
                "run_id": r[0],
                "conversation_id": r[1],
                "date": r[2].replace(tzinfo=UTC).isoformat(),
                "title": r[5] or "历史对话",
                "user_said": r[3][:120],
            }
            for r in selected
        ],
        "next_cursor": selected[-1][0] if len(rows) > len(selected) and selected else None,
    }
