from datetime import UTC

from .search import _rows


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
