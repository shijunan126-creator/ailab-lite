"""Conversation search: tokenization, the per-conversation term index and the query path.

Search matches every conversation a user owns, not a bounded recent window. A row per conversation holds the union of
the terms ever seen in its title, prompts and answers; the database indexes that union with FTS5 (SQLite) or a
trigger-maintained tsvector + GIN index (PostgreSQL). A query first asks that index for candidate conversations, then
scans only those conversations' runs newest-first for the exact text, so a rare phrase does not read every answer in
the account.

Tokenization is deliberately simple and identical for indexing and querying: NFKC + casefold, Latin words (plus their
identifier parts) and CJK bigrams. CJK bigrams keep literal substring matching for Chinese; Latin matching is by word,
which is the documented boundary of this index.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import and_, event, func, literal_column, or_, select, text, tuple_
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .domain.answer_parts import for_history
from .models import Conversation, ConversationSearch, Run, SearchIndexState, new_id

SNIPPET_CHARS = 120
CANDIDATE_PAGE = 50
CANDIDATE_BUDGET = 2000
RUN_MATCH_PAGE = 20
RUN_MATCH_BUDGET = 200
FALLBACK_CONVERSATIONS = 200
MAX_QUERY_TERMS = 48
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_LATIN = re.compile(r"[a-z0-9]+(?:[_.\-/][a-z0-9]+)*")
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
_ID_SEPARATOR = re.compile(r"[_.\-/]+")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def terms(text: str) -> list[str]:
    """Index terms in document order, de-duplicated. Latin words keep their identifier parts; CJK runs become bigrams."""
    folded = _fold(text)
    found: list[str] = []
    for match in _LATIN.finditer(folded):
        word = match.group()
        parts = _ID_SEPARATOR.split(word)
        if len(parts) > 1:
            found.append("_".join(parts))
            found.extend(parts)
        else:
            found.append(word)
    for run in _CJK.findall(folded):
        if len(run) > 1:
            found.extend(run[index : index + 2] for index in range(len(run) - 1))
    seen: set[str] = set()
    result: list[str] = []
    for term in found:
        if len(term) < 2 or term in seen:
            continue
        seen.add(term)
        result.append(term)
    return result


def query_segments(query: str) -> list[str]:
    return [part for part in _fold(query).split() if part]


def query_terms(segments: list[str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        for term in terms(segment):
            if term not in seen:
                seen.add(term)
                found.append(term)
    if len(found) <= MAX_QUERY_TERMS:
        return found
    # Any subset of a true match's terms is safe; sample the whole query instead of only its first words.
    step = math.ceil(len(found) / MAX_QUERY_TERMS)
    return found[::step][:MAX_QUERY_TERMS]


def fold_with_map(text: str) -> tuple[str, list[int]]:
    """Fold the text the same way as the query and remember where each folded code point came from."""
    parts: list[str] = []
    positions: list[int] = []
    for index, char in enumerate(text or ""):
        part = unicodedata.normalize("NFKC", char).casefold()
        parts.append(part)
        positions.extend([index] * len(part))
    return "".join(parts), positions


def contains(text: str, segments: list[str]) -> bool:
    folded, _ = fold_with_map(text)
    return bool(segments) and all(segment in folded for segment in segments)


def _ranges(text: str, segments: list[str], cap: int = 60) -> list[tuple[int, int]]:
    folded, positions = fold_with_map(text)
    found: list[tuple[int, int]] = []
    for segment in segments:
        if not segment:
            continue
        start = 0
        while len(found) < cap:
            at = folded.find(segment, start)
            if at < 0:
                break
            end = at + len(segment)
            if positions:
                found.append((positions[at], positions[end - 1] + 1))
            start = at + max(1, len(segment))
    found.sort()
    merged: list[tuple[int, int]] = []
    for start, end in found:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def snippet_for(text: str, segments: list[str], *, limit: int = SNIPPET_CHARS) -> tuple[str, list[list[int]]]:
    """A short window around the first hit, with every hit inside it marked. Offsets are code points."""
    text = text or ""
    found = _ranges(text, segments)
    if not text:
        return "", []
    if not found:
        return text[:limit], []
    start = max(0, min(found[0][0] - 40, len(text) - limit))
    value = text[start : start + limit]
    highlights: list[list[int]] = []
    for hit_start, hit_end in found:
        left = max(0, hit_start - start)
        right = min(len(value), hit_end - start)
        if left < right:
            highlights.append([left, right])
    return value, highlights


def _lower(dialect: str):
    # The database's C locale only lowercases ASCII. Search semantics must not depend on initdb's host locale;
    # PostgreSQL 16 images include ICU's root locale, and SQLite registers a Python lower() function.
    if dialect == "sqlite":
        return func.ailab_search_lower
    return lambda column_: func.lower(column_.collate("und-x-icu"))


def _like(segment: str) -> str:
    escaped = segment.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def candidate_query(user_id: str, chosen_terms: list[str], dialect: str, *, after=None, limit: int | None = None):
    """Conversations whose union contains every chosen term, newest activity first."""
    if dialect == "sqlite":
        # SQLite would otherwise walk conversations and run the FTS MATCH for each row. MATERIALIZED makes the FTS
        # result the driver, so a rare phrase does not read every conversation (measured 44ms -> 1.7ms at 2k rows).
        match = " AND ".join(f'"{term}"' for term in chosen_terms)
        after_sql = ""
        params: dict = {"user": user_id, "match": match}
        if after is not None:
            after_sql = "AND (c.updated_at, c.id) < (:after_at, :after_id)"
            params.update(after_at=after[0], after_id=after[1])
        limit_sql = ""
        if limit:
            limit_sql = "LIMIT :limit"
            params["limit"] = limit
        sql = f"""
            WITH matched AS MATERIALIZED (
                SELECT rowid AS id FROM conversation_search_fts WHERE conversation_search_fts MATCH :match
            )
            SELECT c.id, c.title, c.project_id, c.updated_at
            FROM matched
            JOIN conversation_search cs ON cs.id = matched.id
            JOIN conversations c ON c.id = cs.conversation_id
            WHERE c.user_id = :user AND c.deleted_at IS NULL AND cs.user_id = :user
            {after_sql}
            ORDER BY c.updated_at DESC, c.id DESC
            {limit_sql}
        """
        return text(sql).bindparams(**params)
    query = (
        select(Conversation.id, Conversation.title, Conversation.project_id, Conversation.updated_at)
        .join(ConversationSearch, ConversationSearch.conversation_id == Conversation.id)
        .where(
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
            ConversationSearch.user_id == user_id,
        )
    )
    # A stored tsvector (maintained by the PostgreSQL trigger) keeps the GIN index attractive to the planner.
    tsquery = func.plainto_tsquery(literal_column("'simple'"), " ".join(chosen_terms))
    query = query.where(ConversationSearch.terms_tsv.op("@@")(tsquery))
    if after is not None:
        query = query.where(tuple_(Conversation.updated_at, Conversation.id) < after)
    query = query.order_by(Conversation.updated_at.desc(), Conversation.id.desc())
    return query.limit(limit) if limit else query


def matching_run_query(conversation_id: str, segments: list[str], dialect: str, *, after=None, limit: int | None = None):
    """The newest runs of one conversation where every segment appears in the prompt or in the answer."""
    lower = _lower(dialect)
    patterns = [_like(segment) for segment in segments]
    # Terms may be split across the user's question and the answer; a run is a document, not two separate ones.
    per_segment = [or_(lower(Run.prompt).like(pattern, escape="\\"), lower(Run.summary).like(pattern, escape="\\")) for pattern in patterns]
    query = select(Run.id, Run.prompt, Run.summary, Run.created_at).where(Run.conversation_id == conversation_id, and_(*per_segment))
    if after is not None:
        query = query.where(tuple_(Run.created_at, Run.id) < after)
    query = query.order_by(Run.created_at.desc(), Run.id.desc())
    return query.limit(limit) if limit else query


def _index_row(db: Session, conversation: Conversation) -> ConversationSearch:
    # autoflush is off, so a row added earlier in this transaction is not visible to a SELECT yet.
    for pending in db.new:
        if isinstance(pending, ConversationSearch) and pending.conversation_id == conversation.id:
            return pending
    row = db.scalar(select(ConversationSearch).where(ConversationSearch.conversation_id == conversation.id))
    if row is None:
        row = ConversationSearch(conversation_id=conversation.id, user_id=conversation.user_id, terms="")
        db.add(row)
    return row


def index_conversation(db: Session, conversation: Conversation, extra_terms=()) -> None:
    """Union the title and any new run terms into the conversation's index row. Never removes a term."""
    if conversation.id is None:
        conversation.id = new_id("cnv")
    if not conversation.user_id:
        return
    row = _index_row(db, conversation)
    incoming = set(terms(conversation.title or "")) | set(extra_terms)
    existing = set(row.terms.split()) if row.terms else set()
    if incoming - existing or row.user_id != conversation.user_id:
        row.terms = " ".join(sorted(existing | incoming))
        row.user_id = conversation.user_id


def refresh_conversation(db: Session, conversation_id: str) -> None:
    """Re-read a conversation after a bulk title update and union the new title into its index row."""
    conversation = db.get(Conversation, conversation_id)
    if conversation is None:
        return
    db.refresh(conversation)
    index_conversation(db, conversation)


def index_run_terms(db: Session, run: Run) -> None:
    if not run.conversation_id:
        return
    conversation = db.get(Conversation, run.conversation_id)
    if conversation is None:
        return
    index_conversation(db, conversation, terms((run.prompt or "") + "\n" + (run.summary or "")))


def _matching_run(db: Session, conversation_id: str, segments: list[str], dialect: str):
    """Return (run_id, match, text) for the newest run whose visible text contains every segment."""
    after = None
    scanned = 0
    fallback = None
    while scanned < RUN_MATCH_BUDGET:
        rows = db.execute(matching_run_query(conversation_id, segments, dialect, after=after, limit=RUN_MATCH_PAGE)).all()
        if not rows:
            break
        for row in rows:
            scanned += 1
            if fallback is None:
                fallback = row
            prompt = row.prompt or ""
            if contains(prompt, segments):
                return row.id, "prompt", prompt
            answer = for_history(row.summary or "", keep_ui=False, limit=len(row.summary or ""))
            if contains(answer, segments):
                return row.id, "answer", answer
            if contains(prompt + "\n" + answer, segments):
                # Split across the question and the answer: show the field that carries more of the query.
                prompt_score = sum(1 for segment in segments if contains(prompt, [segment]))
                answer_score = sum(1 for segment in segments if contains(answer, [segment]))
                if answer_score > prompt_score:
                    return row.id, "answer", answer
                return row.id, "prompt", prompt
        after = (rows[-1].created_at, rows[-1].id)
    if fallback is not None:
        # A database lower() can be broader than Python's casefold (or the hit may sit in UI source). Keep the
        # result rather than pretending nothing matched, but never show the hidden text.
        prompt = fallback.prompt or ""
        if contains(prompt, segments) or all(segment in (prompt or "").lower() for segment in segments):
            return fallback.id, "prompt", prompt
        answer = for_history(fallback.summary or "", keep_ui=False, limit=len(fallback.summary or ""))
        return fallback.id, "answer", answer
    return None


def _item(conversation, match: str, run_id: str | None, text: str, segments: list[str]) -> dict:
    # A title is rendered whole in the row, so its highlight offsets must refer to the whole title, not a window.
    value, highlight = snippet_for(text, segments, limit=len(text) if match == "title" else SNIPPET_CHARS)
    return {
        "conversation_id": conversation.id,
        "title": conversation.title or "",
        "project_id": conversation.project_id,
        "run_id": run_id,
        "match": match,
        "snippet": value,
        "highlight": highlight,
        "updated_at": conversation.updated_at,
    }


def _indexed_search(db: Session, user_id: str, segments: list[str], chosen: list[str], limit: int) -> list[dict]:
    dialect = db.bind.dialect.name
    title_hits: list[dict] = []
    run_hits: list[dict] = []
    after = None
    scanned = 0
    while scanned < CANDIDATE_BUDGET and len(title_hits) < limit and len(run_hits) < limit:
        rows = db.execute(candidate_query(user_id, chosen, dialect, after=after, limit=CANDIDATE_PAGE)).all()
        if not rows:
            break
        for conversation in rows:
            scanned += 1
            title = conversation.title or ""
            if contains(title, segments):
                title_hits.append(_item(conversation, "title", None, title, segments))
            else:
                found = _matching_run(db, conversation.id, segments, dialect)
                if found:
                    run_id, match, text = found
                    run_hits.append(_item(conversation, match, run_id, text, segments))
            if len(title_hits) >= limit or len(run_hits) >= limit:
                break
        after = (rows[-1].updated_at, rows[-1].id)
    best: dict[str, dict] = {}
    for item in title_hits + run_hits:
        current = best.get(item["conversation_id"])
        if current is None or item["match"] == "title":
            best[item["conversation_id"]] = item
    merged = sorted(best.values(), key=lambda item: (item["updated_at"], item["match"] == "title"), reverse=True)
    return merged[:limit]


def _fallback_search(db: Session, user_id: str, segments: list[str], limit: int) -> list[dict]:
    """A query with no indexable term (a single character, punctuation) scans the newest conversations only."""
    dialect = db.bind.dialect.name
    rows = db.execute(
        select(Conversation.id, Conversation.title, Conversation.project_id, Conversation.updated_at)
        .where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(FALLBACK_CONVERSATIONS)
    ).all()
    items: list[dict] = []
    for conversation in rows:
        title = conversation.title or ""
        if contains(title, segments):
            items.append(_item(conversation, "title", None, title, segments))
        else:
            found = _matching_run(db, conversation.id, segments, dialect)
            if found:
                run_id, match, text = found
                items.append(_item(conversation, match, run_id, text, segments))
        if len(items) >= limit:
            break
    return items


def run_search(db: Session, user_id: str, query: str, limit: int = 20) -> list[dict]:
    segments = query_segments(query)
    if not segments:
        return []
    chosen = query_terms(segments)
    if chosen:
        return _indexed_search(db, user_id, segments, chosen, limit)
    return _fallback_search(db, user_id, segments, limit)


def index_pending(db: Session, run_limit: int = 200, conversation_limit: int = 200) -> bool:
    """Index rows written before the index existed. Returns True while a full batch was processed."""
    state = db.get(SearchIndexState, 1)
    if state is None:
        try:
            with db.begin_nested():
                db.add(
                    SearchIndexState(
                        id=1,
                        run_watermark_at=EPOCH,
                        run_watermark_id="",
                        conversation_watermark_at=EPOCH,
                        conversation_watermark_id="",
                    )
                )
                db.flush()
        except IntegrityError:
            pass  # another API process created the watermark first
        state = db.get(SearchIndexState, 1)
        if state is None:
            raise RuntimeError("search index watermark could not be created")
    runs = list(
        db.scalars(
            select(Run)
            .where(tuple_(Run.updated_at, Run.id) > (state.run_watermark_at, state.run_watermark_id))
            .order_by(Run.updated_at, Run.id)
            .limit(run_limit)
        )
    )
    for run in runs:
        index_run_terms(db, run)
    if runs:
        state.run_watermark_at = runs[-1].updated_at
        state.run_watermark_id = runs[-1].id
    conversations = list(
        db.scalars(
            select(Conversation)
            .where(tuple_(Conversation.updated_at, Conversation.id) > (state.conversation_watermark_at, state.conversation_watermark_id))
            .order_by(Conversation.updated_at, Conversation.id)
            .limit(conversation_limit)
        )
    )
    for conversation in conversations:
        index_conversation(db, conversation)
    if conversations:
        state.conversation_watermark_at = conversations[-1].updated_at
        state.conversation_watermark_id = conversations[-1].id
    db.commit()
    return len(runs) == run_limit or len(conversations) == conversation_limit


def _on_before_flush(session: Session, _flush_context, _instances) -> None:
    # The index helpers read before writing. A session configured with autoflush=True must not recurse into flush here.
    autoflush = session.autoflush
    session.autoflush = False
    try:
        new = list(session.new)
        new_set = set(new)
        # Conversations first: a run in the same flush needs its conversation row to exist (and its id assigned).
        for obj in new:
            if isinstance(obj, Conversation):
                index_conversation(session, obj)
        for obj in new + list(session.dirty):
            if isinstance(obj, Run):
                state = sa_inspect(obj)
                if obj in new_set or state.attrs.prompt.history.has_changes() or state.attrs.summary.history.has_changes():
                    index_run_terms(session, obj)
            elif isinstance(obj, Conversation) and obj not in new_set:
                if sa_inspect(obj).attrs.title.history.has_changes():
                    index_conversation(session, obj)
        for obj in list(session.deleted):
            if isinstance(obj, Conversation):
                row = session.scalar(select(ConversationSearch).where(ConversationSearch.conversation_id == obj.id))
                if row is not None:
                    session.delete(row)
    finally:
        session.autoflush = autoflush


event.listens_for(Session, "before_flush")(_on_before_flush)
