"""Summarize the user's question into a title; do not copy its first sentence verbatim.

The Agent persists its independently metered prompt title via an internal compare-and-set.
A completed result may still provide a title or explicit Markdown heading as an empty-title fallback.
"""

from __future__ import annotations

import re

from sqlalchemy import update
from sqlalchemy.orm import Session

from .models import Conversation, Run, RunStatus


def apply_result_title(db: Session, run: Run) -> None:
    if not run.conversation_id or run.status != RunStatus.SUCCEEDED:
        return
    candidate = (run.result or {}).get("conversation_title")
    if not isinstance(candidate, str) or not candidate.strip():
        # Ignore code blocks: a heading in an example is not the answer's title.
        prose = re.sub(r"```.*?```", "", run.summary or "", flags=re.S)
        heading = re.search(r"^#{1,2} +([^\n]+)$", prose, flags=re.M)
        candidate = heading[1] if heading else ""
    title = candidate.strip().strip('# *`"“”')
    if not title or len(title) > 40 or "\n" in title or title in {"总结", "回答", "结果", "分析", "结论"}:
        return
    # Compare-and-set preserves manual names, including a rename racing completion.
    db.execute(
        update(Conversation)
        .where(
            Conversation.id == run.conversation_id,
            Conversation.user_id == run.user_id,
            Conversation.deleted_at.is_(None),
            Conversation.title == "",
        )
        .values(title=title)
    )
