"""Server-side reader for the Portal side pane: a public page's extracted text.

The side pane cannot iframe external sites (most send X-Frame-Options), so it shows what this API
fetches itself: the same public-only client, caps, deadlines and extraction the Agent's read_url
uses. One bounded fetch per call; a click costs at most one page load.
"""

import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import web
from ..deps import current_user
from ..models import User

router = APIRouter(prefix="/v1", tags=["reader"])

READ_TIMEOUT_SECONDS = 45


@router.get("/reader")
async def reader(url: str = Query(min_length=1, max_length=2000), _: User = Depends(current_user)) -> dict:
    """The readable text of one public web page, for the side pane's 网页 view."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid url")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="only http(s) links can be opened in the reader")
    try:
        page = await asyncio.wait_for(web.read_url(url), timeout=READ_TIMEOUT_SECONDS)
    except TimeoutError:
        raise HTTPException(status_code=504, detail=f"page did not finish loading within {READ_TIMEOUT_SECONDS}s")
    except web.WebError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"url": page["url"], "title": page["title"], "text": page["text"], "truncated": page["truncated"]}
