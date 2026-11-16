"""Search and page reading on top of web.py: provenance, per-run dedupe and search excerpts.

web.py owns the public-only transport. This module owns what the Agent adds on top of it:

* a URL that did not come from the user or an earlier tool result gets one search before it is
  refused, so knowing a page address is not a reason to spend a whole extra model round trip;
* a page segment already read in this run is answered from the transcript instead of fetched twice;
* the first search hits carry a short query-relevant excerpt, which is usually enough to answer.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .. import web
from ..config import settings
from . import net_tools, web_excerpts

EXCERPT_RESULTS = 3
EXCERPT_PAGE_CHARS = 8000
MAX_WEB_READS = 64
READ_URL_DEFAULT_CHARS = 8000
READ_URL_MAX_CHARS = 20000


def normalize_url(url: str) -> str:
    """One identity for a page inside a run: scheme and host lowercase, no fragment, no default port."""
    raw = url.strip().rstrip(".,;:!?")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return raw
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not host:
        return raw
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _read_key(url: str, start: int) -> str:
    return f"{normalize_url(url)}\x00{start}"


def _brief(hits: list[dict]) -> list[dict]:
    return [
        {key: str(hit.get(key) or "")[:2000] for key in ("title", "url", "snippet")}
        for hit in hits
        if isinstance(hit, dict) and str(hit.get("url") or "").startswith(("http://", "https://"))
    ]


async def _search_source(ctx, url: str) -> tuple[list[dict], str | None]:
    """One search for a URL that failed provenance; the matched result URL is the only thing readable."""
    ctx.budget.request()
    found = await web.search(url[:300], settings.web_max_results)
    wanted = normalize_url(url)
    match = next((hit["url"] for hit in found if isinstance(hit, dict) and normalize_url(str(hit.get("url") or "")) == wanted), None)
    return _brief(found), match


async def read_page(ctx, url: str, *, start: int = 0, max_chars: int | None = None) -> dict:
    """Read one page segment, or answer a repeat from the run's transcript.

    Raises ``net_tools.NetToolError`` for a bad or over-budget call; a URL the source rule does not
    know is returned as an error result that also carries the supplemental search hits.
    """
    duplicate = (ctx.web_reads or {}).get(_read_key(url, start))
    if duplicate:
        next_start = duplicate.get("next_start")
        tail = f"要后续内容请用 start={next_start}" if next_start is not None else "这一段已到末尾"
        note = f"第 {duplicate['step']} 步已读过这一段，内容见上文；{tail}"
        return {"ok": True, "url": normalize_url(url), "duplicate": True, "note": note}
    via_search = False
    try:
        net_tools.check_source(url, net_tools.sources_from(ctx.messages))
    except net_tools.NetToolError as exc:
        found, match = await _search_source(ctx, url)
        if match is None:
            return {
                "ok": False,
                "error": str(exc),
                "results": found,
                "note": "URL 不在用户消息或已有结果里；以上是按该 URL 搜索到的结果，选一个再读",
            }
        url, via_search = match, True
    ctx.budget.request()
    limit = max(1000, min(int(max_chars or READ_URL_DEFAULT_CHARS), READ_URL_MAX_CHARS))
    # The start argument reaches web.read_url only when it matters: a default read keeps working with
    # the two-argument form while the segmented reader is rolled out.
    kwargs: dict[str, Any] = {"max_chars": limit}
    if start:
        kwargs["start"] = start
    page = {**await web.read_url(url, **kwargs), "ok": True}
    if via_search:
        page["via_search"] = True
    _remember_read(ctx, url, start, page.get("next_start"))
    return page


def _remember_read(ctx, url: str, start: int, next_start: int | None) -> None:
    reads = ctx.web_reads
    reads[_read_key(url, start)] = {"step": ctx.step + 1, "next_start": next_start}
    for key in list(reads)[: max(0, len(reads) - MAX_WEB_READS)]:
        reads.pop(key, None)


async def search(ctx, query: str, count: int, kind: str) -> list[dict]:
    """Search, then attach a bounded excerpt to the top web hits (images keep their own shape)."""
    ctx.budget.request()
    found = await web.search(query, count, kind)
    if kind == "web" and found and settings.web_search_provider != "mock":
        await _add_excerpts(found, query, ctx)
    return found


# Only real language codes count as a locale root: a short path such as /go or /faq is often the very page asked about.
_LANGUAGES = frozenset(
    "ar bg cs da de el en es et fa fi fr he hi hu id it ja ko lt lv ms nb nl no pl pt ro ru sk sl sr sv th tr uk vi zh".split()
)
_LOCALE = re.compile(r"([a-z]{2})(?:[-_](?:[a-z]{2}|hans|hant))?")


def _homepage(hit: dict) -> bool:
    """A site root or a bare locale root (/zh, /en-us, /zh-hans) rarely answers a query, so it is excerpted last."""
    path = urlsplit(str(hit.get("url") or "")).path.strip("/").lower()
    match = _LOCALE.fullmatch(path)
    return not path or (match is not None and match.group(1) in _LANGUAGES)


async def _add_excerpts(results: list[dict], query: str, ctx) -> None:
    candidates = iter(sorted(results, key=_homepage))

    async def one(hit: dict) -> bool:
        ctx.budget.request()
        page = await web.read_url(str(hit["url"]), max_chars=EXCERPT_PAGE_CHARS)
        text = str(page.get("text") or "")
        if page.get("challenged") or not text.strip():
            return False
        full = len(text) <= web_excerpts.FULL_TEXT_CHARS and not page.get("truncated") and page.get("next_start") is None
        excerpt = text if full else web_excerpts.excerpt(text, query)
        if not excerpt:
            return False
        hit["excerpt"] = excerpt
        if full:
            hit["full_text"] = True
            _remember_read(ctx, str(hit["url"]), 0, None)
        return True

    pending = set()
    successes = 0
    deadline = asyncio.get_running_loop().time() + settings.web_excerpt_deadline_seconds
    try:
        while successes < EXCERPT_RESULTS and asyncio.get_running_loop().time() < deadline:
            while len(pending) < EXCERPT_RESULTS - successes:
                hit = next(candidates, None)
                if hit is None:
                    break
                pending.add(asyncio.create_task(one(hit)))
            remaining = deadline - asyncio.get_running_loop().time()
            if not pending or remaining <= 0:
                break
            done, pending = await asyncio.wait(pending, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                break
            for task in done:
                if not task.cancelled() and task.exception() is None and task.result():
                    successes += 1
    finally:
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
