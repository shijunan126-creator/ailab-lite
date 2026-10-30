"""Derive conversation mode without changing historical turns or fork choices."""

from fastapi import HTTPException
from sqlalchemy import select

from ..models import Run


def fixed_conversation_mode(first_own_run, forked_from):
    """Copied fork turns are history; only an own turn fixes the new conversation."""
    if first_own_run is not None:
        return (first_own_run.options or {}).get("mode", "work")
    return (forked_from or {}).get("mode")


def resolve_conversation_mode(db, conversation, requested, *, turn_mode=None, requires_work=False):
    if turn_mode is not None:
        # Versions preserve their original mode, including historical mixed-mode data.
        fixed = turn_mode
    elif conversation is not None:
        first = db.scalar(
            select(Run)
            .where(Run.conversation_id == conversation.id, Run.result["forked_from_run_id"].as_string().is_(None))
            .order_by(Run.created_at, Run.id)
            .limit(1)
        )
        fixed = fixed_conversation_mode(first, conversation.forked_from)
    else:
        fixed = None
    if fixed is not None and ((requested is not None and requested != fixed) or (requires_work and fixed != "work")):
        raise HTTPException(409, {"code": "conversation_mode_fixed", "message": "对话模式在开始后不能切换"})
    return "work" if requires_work else requested or fixed or "work"
