"""Server-side view of a public page for the Portal side pane: its original look.

The side pane cannot iframe external sites directly (most send X-Frame-Options), so /reader/render
re-serves the fetched HTML with the frame-blocking headers gone and a <base> pointed at the site —
the sandboxed iframe then shows the original page. It uses the Agent's public-only client, caps and
deadlines; static first, then at most one isolated browser snapshot when needed.
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


SNAPSHOT_HEADERS = {
    "Content-Security-Policy": "sandbox allow-popups; script-src 'none'; frame-src 'none'; object-src 'none'; form-action 'none'",
    "Cache-Control": "private, no-store",
    "Referrer-Policy": "no-referrer",
}


def _reader_failure(url: str, reason: str, status: int) -> Response:
    title = "暂时无法预览此网页"
    if isinstance(reason, str) and any(f"HTTP {code}" in reason for code in (401, 403, 412, 429)):
        message = "网站拒绝了平台的读取请求，可能需要登录或验证。请使用右上角 ↗ 在新标签打开原站。"
    elif status == 504 or "timed out" in reason or "did not finish" in reason:
        message = "网页加载超时，请稍后重试，或使用右上角 ↗ 打开原站。"
    else:
        message = "暂时无法读取此页面，请稍后重试，或使用右上角 ↗ 打开原站。"
    html = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title><style>body{{font:15px/1.7 system-ui;margin:0;padding:32px;color:#344054;background:#f8f9fb}}"
        "h1{font-size:20px}p{overflow-wrap:anywhere}small{color:#667085}</style>"
        f"<h1>{title}</h1><p>{message}</p><p><small>{escape(url)}</small></p></html>"
    )
    return Response(content=html, status_code=status, media_type="text/html; charset=utf-8", headers=SNAPSHOT_HEADERS)


async def _preview_page(url: str) -> dict:
    try:
        return await web.fetch_html(url)
    except web.WebStatusError as exc:
        if exc.status not in web.RENDER_STATUSES or not web.settings.crawler_url:
            raise
    except web.WebFetchError:
        if not web.settings.crawler_url:
            raise
    # Ordinary public-fetch rejections (including private destinations and TLS failures) never
    # reach this path. The browser retains its independent egress proxy and the same URL checks.
    return await web.fetch_rendered_html(url)


@router.get("/reader/render")
async def reader_render(url: str = Query(min_length=1, max_length=2000), _: User = Depends(current_user)) -> Response:
    """Static snapshot first, one bounded browser retry on eligible upstream/transport failures."""
    _check_url(url)
    try:
        page = await asyncio.wait_for(_preview_page(url), timeout=READ_TIMEOUT_SECONDS)
    except TimeoutError:
        return _reader_failure(url, "page reader timed out", 504)
    except web.WebError as exc:
        return _reader_failure(url, str(exc), 502)
    if page["content_type"] not in ("text/html", "application/xhtml+xml"):
        raise HTTPException(status_code=415, detail="not an HTML page")
    html = _FrameMarkup(page["text"]).render(page["url"])
    return Response(content=html, media_type="text/html; charset=utf-8", headers=SNAPSHOT_HEADERS)
