"""Conversation tombstones apply to every content path, including retained Runs."""

from sqlalchemy import or_, select

from .models import Conversation, Run


def live_run_predicate():
    return or_(
        Run.conversation_id.is_(None),
        select(Conversation.id)
        .where(Conversation.id == Run.conversation_id, Conversation.user_id == Run.user_id, Conversation.deleted_at.is_(None))
        .exists(),
    )


def conversation_available(db, run):
    if not run.conversation_id:
        return True
    conversation = db.get(Conversation, run.conversation_id, populate_existing=True)
    return bool(conversation and conversation.user_id == run.user_id and conversation.deleted_at is None)
