"""Server-side reader for the Portal side pane: a public page's original look or extracted blocks.

The side pane cannot iframe external sites directly (most send X-Frame-Options), so /reader/render
re-serves the fetched HTML with the frame-blocking headers gone and a <base> pointed at the site —
the sandboxed iframe then shows the original page. /reader is the fallback: the same fetch run
through ailab_platform.reader, which picks the article out of the page (chrome dropped, structure
kept). Both share the Agent's public-only client, caps and deadlines; one bounded fetch per call.
"""

import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import web
from ..deps import current_user
from ..models import User

router = APIRouter(prefix="/v1", tags=["reader"])

READ_TIMEOUT_SECONDS = 45


def _check_url(url: str) -> None:
    if not web.enabled():  # the same switch that gives the Agent its web tools
        raise HTTPException(status_code=404, detail="web access is not enabled")
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid url")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="only http(s) links can be opened in the reader")


@router.get("/reader")
async def reader(url: str = Query(min_length=1, max_length=2000), _: User = Depends(current_user)) -> dict:
    """The readable blocks of one public web page, for the side pane's 正文 view."""
    _check_url(url)
    try:
        page = await asyncio.wait_for(web.read_page(url), timeout=READ_TIMEOUT_SECONDS)
    except TimeoutError:
        raise HTTPException(status_code=504, detail=f"page did not finish loading within {READ_TIMEOUT_SECONDS}s")
    except web.WebError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"url": page["url"], "title": page["title"], "blocks": page["blocks"], "truncated": page["truncated"]}


# Clicks inside the frame open a new tab instead of navigating the frame into a site that would
# refuse to be framed; capture phase runs before the page's own handlers.
_LINK_OUT = (
    "<script>document.addEventListener('click',function(e){var t=e.target;"
    "var a=t&&t.closest?t.closest('a[href]'):null;if(a)a.target='_blank'},true)</script>"
)


@router.get("/reader/render")
async def reader_render(url: str = Query(min_length=1, max_length=2000), _: User = Depends(current_user)) -> Response:
    """The original page, re-served so the side pane can show it: same bounded fetch, frame-blocking
    headers stripped (this response is ours), base pointed at the site so relative assets load."""
    _check_url(url)
    try:
        page = await asyncio.wait_for(web.fetch_html(url), timeout=READ_TIMEOUT_SECONDS)
    except TimeoutError:
        raise HTTPException(status_code=504, detail=f"page did not finish loading within {READ_TIMEOUT_SECONDS}s")
    except web.WebError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if page["content_type"] not in ("text/html", "application/xhtml+xml"):
        raise HTTPException(status_code=415, detail="not an HTML page; use the extracted view")
    html = page["text"]
    base = f'<base href="{page["url"]}">{_LINK_OUT}'
    if "<head" in html.lower():
        head = html.lower().index("<head")
        gt = html.index(">", head) + 1
        html = html[:gt] + base + html[gt:]
    else:
        html = base + html
    return Response(content=html, media_type="text/html; charset=utf-8")
