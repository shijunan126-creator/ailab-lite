"""Bounded path traversal shared by public history, model context and workspace restoration."""

from fastapi import HTTPException
from sqlalchemy import literal, select

from .models import Run, RunStatus

MAX_DEPTH = 500


def ancestor_query(conversation_id, leaf_id):
    path = (
        select(Run.id, Run.parent_run_id, literal(1).label("depth"))
        .where(Run.conversation_id == conversation_id, Run.id == leaf_id)
        .cte("ancestor_path", recursive=True)
    )
    parent = select(Run.parent_run_id).where(Run.id == path.c.parent_run_id).scalar_subquery()
    parent_conversation = select(Run.conversation_id).where(Run.id == path.c.parent_run_id).scalar_subquery()
    # Correlated primary-key probes avoid a planner choosing to scan all conversation branches at every depth.
    return path.union_all(
        select(path.c.parent_run_id, parent, path.c.depth + 1).where(
            path.c.parent_run_id.is_not(None),
            path.c.depth <= MAX_DEPTH,
            parent_conversation == conversation_id,
        )
    )


def path_ids(db, conversation_id, leaf_id):
    if not leaf_id:
        return []
    path = ancestor_query(conversation_id, leaf_id)
    ids = list(db.scalars(select(path.c.id).order_by(path.c.depth.desc())))
    if len(ids) > MAX_DEPTH or len(ids) != len(set(ids)):
        raise HTTPException(422, "对话路径最多500轮")
    return ids


def path_runs(db, conversation_id, leaf_id):
    ids = path_ids(db, conversation_id, leaf_id)
    rows = {r.id: r for r in db.scalars(select(Run).where(Run.conversation_id == conversation_id, Run.id.in_(ids)))}
    return [rows[id_] for id_ in ids]


def require_idle(db, conversation_id):
    if db.scalar(select(Run.id).where(Run.conversation_id == conversation_id, Run.status.not_in(RunStatus.TERMINAL)).limit(1)):
        # `code` is the stable contract the Portal branches on; `message` is what a human reads.
        raise HTTPException(409, {"code": "conversation_busy", "message": "上一条消息还在回答中，请稍候或先停止它"})


def siblings_query(conversation_id, parent_id):
    return select(Run.id).where(Run.conversation_id == conversation_id, Run.parent_run_id == parent_id).order_by(Run.created_at, Run.id)
