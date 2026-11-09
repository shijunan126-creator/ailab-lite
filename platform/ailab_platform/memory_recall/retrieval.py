"""Bounded lexical retrieval across facts and history; no automatic injection."""

from datetime import UTC, datetime

from . import text
from .policy import model_view
from .search import _rows, search_history


@text.cached_doc
def item_doc(identity, updated_at, content, entities, page):
    return text.build_doc(identity, (content, 1.0), (" ".join(entities), 1.0), (page, 0.5))


def search_memory(db, user, run, document, request, project_id, mode):
    entries = model_view(document, project_id, mode=mode, as_of=request.as_of)["entries"]
    entries = [
        e
        for e in entries
        if (not request.before or datetime.fromisoformat(e["observed_at"]).replace(tzinfo=UTC) < request.before)
        and (not request.after or datetime.fromisoformat(e["observed_at"]).replace(tzinfo=UTC) >= request.after)
    ]
    query = text.query_tokens(request.query)
    from sqlalchemy import or_, select

    from ..models import MemoryEntity, MemoryEntityAlias, Run
    from .guard import model_text
    from .store import stamp

    scopes = [MemoryEntity.project_id == project_id]
    if project_id and mode == "shared":
        scopes.append(MemoryEntity.project_id.is_(None))
    matched_entities = list(
        db.scalars(
            select(MemoryEntity.canonical)
            .join(MemoryEntityAlias, MemoryEntityAlias.entity_id == MemoryEntity.id)
            .where(MemoryEntityAlias.user_id == user.id, MemoryEntityAlias.name.in_([request.query.casefold(), *query]), or_(*scopes))
            .distinct()
        )
    )
    for canonical in matched_entities:
        query.extend(text.tokens(canonical))
    docs = [item_doc(e["id"], e.get("updated_at"), e["content"], tuple(e.get("entities", [])), e.get("page", "")) for e in entries]
    by_id = {e["id"]: e for e in entries}
    now = datetime.now(UTC)
    import math

    def score(hit):
        entry = by_id[hit.key]
        age = max(0, (now - stamp(entry.get("reinforced_at") or entry.get("observed_at"), now)).total_seconds() / 86400)
        bonus = bool(set(entry.get("entities", [])) & set(matched_entities))
        return hit.score * (1 + 0.25 * math.exp(-math.log(2) * age / 45)) + bonus

    hits = sorted((h for h in text.search(list(dict.fromkeys(query)), docs) if h.coverage >= 0.3), key=lambda h: (-score(h), h.key))
    by_id = {e["id"]: e for e in entries}
    results = []
    for rank, hit in enumerate(hits):
        e = by_id[hit.key]
        results.append(
            (
                1 / (61 + rank),
                {
                    "source": "memory",
                    "id": e["id"],
                    "content": model_text(e["content"])[:120],
                    "date": e["observed_at"],
                    "run_ids": e.get("evidence_run_ids", []),
                },
            )
        )
    if document.get("reference_history", True):
        history = search_history(
            db,
            user,
            " ".join([request.query, *matched_entities]),
            limit=10,
            exclude_conversation_id=run.conversation_id,
            project_id=project_id,
            before=request.before,
            after=request.after,
            order=request.order,
        )
        for rank, result in enumerate(history["results"]):
            results.append((1 / (61 + rank), {**result, "source": "history"}))
    results.sort(key=lambda pair: (-pair[0], pair[1].get("id", pair[1].get("run_id", ""))))
    if getattr(request, "order", None) == "asc":
        results.sort(key=lambda pair: (pair[1]["date"], pair[1].get("id", pair[1].get("run_id", ""))))
    ids = {rid for _, result in results for rid in result.get("run_ids", [])}
    conversations = (
        dict(db.execute(select(Run.id, Run.conversation_id).where(Run.user_id == user.id, Run.id.in_(ids))).all()) if ids else {}
    )
    counts, out, fingerprints = {}, [], set()
    for _, result in results:
        fingerprint = result.get("content") if result["source"] == "memory" else (result.get("user_said"), result.get("assistant_replied"))
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        convs = {conversations[rid] for rid in result.get("run_ids", []) if conversations.get(rid)}
        if result.get("conversation_id"):
            convs.add(result["conversation_id"])
        if any(counts.get(cid, 0) >= 2 for cid in convs):
            continue
        for cid in convs:
            counts[cid] = counts.get(cid, 0) + 1
        days = max(0, (now - stamp(result["date"])).days)
        result["relative_time"] = "今天" if days == 0 else f"{days} 天前"
        if result["source"] == "history":
            for field in ("conversation_title", "user_said", "assistant_replied"):
                result[field] = model_text(result.get(field, ""))
        out.append(result)
        if len(out) == 5:
            break
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
