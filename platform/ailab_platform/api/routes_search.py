"""Owner-scoped search over every conversation the user owns.

The candidate index lives in :mod:`ailab_platform.search_index`; this route validates the query and serializes the
best matches. There is no continuation: a query searches the whole account and returns its top ``limit`` matches.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..deps import current_user, get_db
from ..models import User
from ..schemas import ConversationSearchResult
from ..search_index import SearchTimeout, run_search

router = APIRouter(prefix="/v1/search", tags=["search"])


@router.get("", response_model=ConversationSearchResult)
def search_conversations(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = q.strip()
    if not query:
        raise HTTPException(422, "搜索词不能为空")
    try:
        return {"items": run_search(db, user.id, query, limit)}
    except SearchTimeout as exc:
        raise HTTPException(503, str(exc)) from exc
