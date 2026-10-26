"""Server-side reader for the Portal side pane: a public page's original look or extracted blocks.

The side pane cannot iframe external sites directly (most send X-Frame-Options), so /reader/render
re-serves the fetched HTML with the frame-blocking headers gone and a <base> pointed at the site —
the sandboxed iframe then shows the original page. /reader is the fallback: the same fetch run
through ailab_platform.reader, which picks the article out of the page (chrome dropped, structure
kept). Both share the Agent's public-only client, caps and deadlines; one bounded fetch per call.
"""

import asyncio
from html import escape
from html.parser import HTMLParser
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


class _FrameMarkup(HTMLParser):
    """Remove document-controlled base/refresh tags without parsing or rebuilding page content."""

    def __init__(self, html: str) -> None:
        super().__init__(convert_charrefs=False)
        self.html = html
        self.offsets = [0]
        for line in html.split("\n"):
            self.offsets.append(self.offsets[-1] + len(line) + 1)
        self.removals: list[tuple[int, int]] = []
        self.head_end: int | None = None
        self.document_start = 0

    def handle_starttag(self, tag, attrs):
        line, col = self.getpos()
        start = self.offsets[line - 1] + col
        end = start + len(self.get_starttag_text())
        if tag == "head" and self.head_end is None:
            self.head_end = end
        elif tag == "html":
            self.document_start = end
        if tag == "base" or (tag == "meta" and (dict(attrs).get("http-equiv") or "").lower() == "refresh"):
            self.removals.append((start, end))

    handle_startendtag = handle_starttag

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            line, col = self.getpos()
            self.document_start = self.offsets[line - 1] + col + len(decl) + 3

    def render(self, url: str) -> str:
        self.feed(self.html)
        html = self.html
        base = f'<base href="{escape(url, quote=True)}" target="_blank">'
        position = self.head_end if self.head_end is not None else self.document_start
        edits = [(start, end, "") for start, end in self.removals]
        edits.append((position, position, base))
        parts: list[str] = []
        cursor = 0
        for start, end, value in sorted(edits):
            parts.extend((html[cursor:start], value))
            cursor = end
        parts.append(html[cursor:])
        return "".join(parts)


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
    html = _FrameMarkup(page["text"]).render(page["url"])
    # Preserve HTML/CSS while blocking scripts, subframes and form submission. Site hydration and
    # redirects otherwise escape the fetched snapshot or replace its article with a consent page.
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": "sandbox allow-popups; script-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'",
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
        },
    )
