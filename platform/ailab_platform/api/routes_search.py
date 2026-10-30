"""Owner-scoped search with resumable bounded scans, including inactive branches."""

import base64
import hashlib
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select, tuple_
from sqlalchemy.orm import Session, load_only

from ..deps import current_user, get_db
from ..domain.answer_parts import for_history
from ..models import Conversation, Run, User
from ..schemas import ConversationSearchResult

router = APIRouter(prefix="/v1/search", tags=["search"])
CONVERSATION_BATCH = 50
RUN_BATCH = 100
RUN_BUDGET = 1000


def conversation_scan(user_id, before=None):
    query = (
        select(Conversation)
        .options(load_only(Conversation.id, Conversation.title, Conversation.project_id, Conversation.updated_at))
        .where(Conversation.user_id == user_id, Conversation.deleted_at.is_(None))
    )
    if before:
        query = query.where(tuple_(Conversation.updated_at, Conversation.id) < (datetime.fromisoformat(before[0]), before[1]))
    return query.order_by(Conversation.updated_at.desc(), Conversation.id.desc()).limit(CONVERSATION_BATCH + 1)


def run_scan(conversation_id, before=None):
    query = select(Run.id, Run.prompt, Run.summary, Run.created_at).where(Run.conversation_id == conversation_id)
    if before:
        query = query.where(tuple_(Run.created_at, Run.id) < (datetime.fromisoformat(before[0]), before[1]))
    return query.order_by(Run.created_at.desc(), Run.id.desc()).limit(RUN_BATCH).subquery("search_window")


def search_match_query(window, q, dialect):
    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    # The database's C locale only lowercases ASCII. Search semantics must not
    # depend on initdb's host locale; PostgreSQL 16 images include ICU's root locale.
    lower = func.ailab_search_lower if dialect == "sqlite" else lambda column: func.lower(column.collate("und-x-icu"))
    prompt_hit = lower(window.c.prompt).like(pattern.lower(), escape="\\")
    answer_hit = lower(window.c.summary).like(pattern.lower(), escape="\\")
    text_column = case((prompt_hit, window.c.prompt), else_=window.c.summary)
    # Only a small slice of a matching answer crosses the DB boundary, regardless of answer size.
    position = func.instr(lower(text_column), q.lower()) if dialect == "sqlite" else func.strpos(lower(text_column), q.lower())
    left = case((position > 40, position - 40), else_=1)
    return (
        select(
            window.c.id,
            prompt_hit.label("prompt_hit"),
            func.substr(text_column, left, 120).label("snippet"),
            (position - left).label("hit_start"),
            case((window.c.summary.contains("```openui"), window.c.summary), else_=None).label("ui_summary"),
        )
        .where(prompt_hit | answer_hit)
        .order_by(window.c.created_at.desc(), window.c.id.desc())
        .limit(1)
    )


def snippet(text, needle):
    # Preserve offsets in the original Unicode string even when casefold changes length (e.g. capital sharp S).
    folded, positions = [], []
    for index, char in enumerate(text):
        part = char.casefold()
        folded.append(part)
        positions.extend([index] * len(part))
    position = "".join(folded).find(needle.casefold())
    if position < 0:
        return text[:120], []
    start, end = positions[position], positions[position + len(needle.casefold()) - 1] + 1
    left = max(0, start - 40)
    value = text[left : left + 120]
    return value, [[start - left, min(end - left, len(value))]]


def _cursor(state):
    return base64.urlsafe_b64encode(json.dumps(state, separators=(",", ":")).encode()).decode().rstrip("=")


@router.get("", response_model=ConversationSearchResult)
def search_conversations(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None, max_length=2048),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    fingerprint = hashlib.sha256((user.id + "\0" + q).encode()).hexdigest()
    state = {"key": fingerprint, "before": None, "current": None, "run_before": None}
    if cursor:
        try:
            state = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            if state["key"] != fingerprint or set(state) != {"key", "before", "current", "run_before"}:
                raise ValueError()
            for key in ("before", "run_before"):
                if state[key] is not None:
                    if len(state[key]) != 2 or not isinstance(state[key][1], str):
                        raise ValueError()
                    datetime.fromisoformat(state[key][0])
            if state["current"] is not None and not isinstance(state["current"], str):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeDecodeError):
            raise HTTPException(422, "搜索游标无效") from None
    items, work = [], 0
    conversations = list(db.scalars(conversation_scan(user.id, state["before"])))
    for conv in conversations[:CONVERSATION_BATCH]:
        # A continuation is valid only within the owner's ordered scan; forged IDs cannot broaden the query.
        continuing = state["current"] == conv.id
        if state["current"] and not continuing:
            raise HTTPException(422, "搜索游标已过期")
        title_snippet, highlight = snippet(conv.title, q)
        match, run_id, value = None, None, None
        if highlight:
            match, value = "title", title_snippet
        else:
            while work < RUN_BUDGET:
                window = run_scan(conv.id, state["run_before"] if continuing else None)
                row = db.execute(search_match_query(window, q, db.bind.dialect.name)).first()
                work += RUN_BATCH
                if row:
                    match, run_id = ("prompt" if row.prompt_hit else "answer"), row.id
                    answer = (
                        for_history(row.ui_summary, keep_ui=False, limit=len(row.ui_summary))
                        if row.ui_summary and not row.prompt_hit
                        else None
                    )
                    value, highlight = snippet(answer if answer is not None else row.snippet, q)
                    if not highlight and answer is None:  # A query may exceed the 120-character snippet window.
                        start = max(0, row.hit_start)
                        highlight = [[start, min(len(value), start + len(q))]]
                    break
                tail = db.execute(select(window.c.id, window.c.created_at).order_by(window.c.created_at, window.c.id).limit(1)).first()
                count = db.scalar(select(func.count()).select_from(window))
                if count < RUN_BATCH:
                    state.update(current=None, run_before=None)
                    break
                state.update(current=conv.id, run_before=[tail.created_at.isoformat(), tail.id])
                continuing = True
            if not match and work >= RUN_BUDGET and state["current"]:
                return {"items": items, "next_cursor": _cursor(state)}
        if match:
            items.append(
                {
                    "conversation_id": conv.id,
                    "title": conv.title,
                    "project_id": conv.project_id,
                    "run_id": run_id,
                    "match": match,
                    "snippet": value,
                    "highlight": highlight,
                    "updated_at": conv.updated_at,
                }
            )
        state.update(before=[conv.updated_at.isoformat(), conv.id], current=None, run_before=None)
        if len(items) >= limit or work >= RUN_BUDGET:
            return {"items": items, "next_cursor": _cursor(state)}
    return {"items": items, "next_cursor": _cursor(state) if len(conversations) > CONVERSATION_BATCH else None}
