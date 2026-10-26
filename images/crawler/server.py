"""POST /read {url, max_chars} -> the page's main content as Markdown, rendered by Crawl4AI.

The caller (the Agent) has already checked that the URL is public and came from a legitimate
source. This process only renders: it holds no platform credentials and every connection the
browser makes goes through the egress proxy, which refuses non-public addresses. A fresh browser
per request keeps no cookies or storage between pages and returns its memory when done.
"""

import asyncio
import hmac
import os
import re
import time
from pathlib import Path

from aiohttp import web
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.content_filter_strategy import PruningContentFilter
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

TOKEN = os.environ.get("AILAB_CRAWLER_TOKEN", "")
PROXY = os.environ.get("AILAB_CRAWLER_PROXY", "")
PORT = int(os.environ.get("AILAB_CRAWLER_PORT", "8090"))
PAGE_TIMEOUT_MS = int(os.environ.get("AILAB_CRAWLER_PAGE_TIMEOUT_MS", "20000"))
READ_TIMEOUT_SECONDS = PAGE_TIMEOUT_MS / 1000 + 10
MAX_PAGES = int(os.environ.get("AILAB_CRAWLER_MAX_PAGES", "2"))
MAX_CHARS = 60_000
MAX_LINKS = 50
CHALLENGE = re.compile(
    r"^(?:just a moment[.!…]*|attention required[!！]?(?:\s*\|\s*cloudflare)?|access denied|verify (?:that )?you are human)[.!…\s]*$",
    re.I,
)
TEXT_TYPES = (
    "text/html",
    "application/xhtml+xml",
    "text/plain",
    "text/markdown",
    "application/json",
    "application/xml",
    "text/xml",
)

slots = asyncio.Semaphore(MAX_PAGES)


def browser_config() -> BrowserConfig:
    return BrowserConfig(
        headless=True,
        verbose=False,
        ignore_https_errors=False,
        proxy_config={"server": PROXY},
        java_script_enabled=True,
        accept_downloads=False,
        text_mode=False,
        extra_args=[
            "--disable-gpu",
            "--no-first-run",
            "--disable-dev-shm-usage",
            "--disable-features=ServiceWorker",
        ],
    )


def run_config() -> CrawlerRunConfig:
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        markdown_generator=DefaultMarkdownGenerator(content_filter=PruningContentFilter(threshold=0.48, threshold_type="fixed")),
        excluded_tags=["nav", "footer", "aside", "form", "script", "style", "noscript"],
        remove_overlay_elements=True,
        exclude_social_media_links=True,
        wait_until="domcontentloaded",
        delay_before_return_html=1.0,
        page_timeout=PAGE_TIMEOUT_MS,
        verbose=False,
    )


def links_of(result) -> list[str]:
    out: list[str] = []
    for group in ("internal", "external"):
        for link in (result.links or {}).get(group, []):
            href = str(link.get("href") or "").split("#")[0]
            if href.startswith(("http://", "https://")) and len(href) <= 2000 and href not in out:
                out.append(href)
            if len(out) >= MAX_LINKS:
                return out
    return out


async def read(request: web.Request) -> web.Response:
    if not TOKEN or not hmac.compare_digest(request.headers.get("Authorization", ""), f"Bearer {TOKEN}"):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        body = await request.json()
        url = str(body["url"])
        limit = max(1000, min(int(body.get("max_chars") or 20_000), MAX_CHARS))
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "expected {url, max_chars}"}, status=400)
    if slots.locked():
        return web.json_response({"error": "crawler is busy"}, status=503)
    async with slots:
        started = time.monotonic()
        crawler = AsyncWebCrawler(config=browser_config())
        try:
            try:
                async with asyncio.timeout(READ_TIMEOUT_SECONDS):
                    await crawler.__aenter__()
                    result = await crawler.arun(url, config=run_config())
            finally:
                # __aexit__ is never called when __aenter__ times out. Even a partially
                # started Playwright driver must be closed, before releasing the slot.
                async with asyncio.timeout(5):
                    await crawler.__aexit__(None, None, None)
        except TimeoutError:
            return web.json_response({"error": "page did not finish loading"}, status=504)
        except Exception as exc:  # a broken page must not take the server down
            return web.json_response({"error": f"render failed: {type(exc).__name__}"}, status=502)
    # Crawl4AI success means the browser ran, including HTTP error pages from the proxy.
    if not result.success or (result.status_code or 0) >= 400:
        return web.json_response(
            {
                "error": (result.error_message or "render failed")[:300],
                "status": result.status_code,
            },
            status=502,
        )
    headers = {k.lower(): v for k, v in (result.response_headers or {}).items()}
    ctype = (headers.get("content-type") or "text/html").split(";")[0].strip().lower()
    if ctype not in TEXT_TYPES:
        return web.json_response(
            {"error": f"unsupported content type: {ctype}; use download_url for files"},
            status=415,
        )
    markdown = result.markdown
    # The pruned view drops navigation and boilerplate; a page it empties (short or unusual layout) keeps the full text.
    text = (getattr(markdown, "fit_markdown", "") or "").strip() or (getattr(markdown, "raw_markdown", "") or str(markdown or "")).strip()
    title = str((result.metadata or {}).get("title") or "")[:300]
    if not text or CHALLENGE.fullmatch(title.strip()) or CHALLENGE.fullmatch(text.strip()):
        return web.json_response(
            {"error": "page returned an access challenge or no readable text"},
            status=502,
        )
    truncated = len(text) > limit
    if truncated:
        text = text[:limit].rstrip()
    return web.json_response(
        {
            "url": result.redirected_url or result.url,
            "title": title,
            "status": result.status_code,
            "content_type": ctype,
            "markdown": text,
            "truncated": truncated,
            "links": links_of(result),
            "seconds": round(time.monotonic() - started, 2),
        }
    )


async def health(_: web.Request) -> web.Response:
    # A live HTTP server cannot render when its cgroup has no room for Chromium.
    try:
        maximum = Path("/sys/fs/cgroup/pids.max").read_text().strip()
        current = int(Path("/sys/fs/cgroup/pids.current").read_text())
        if maximum != "max" and int(maximum) - current < 16:
            return web.json_response({"ok": False, "error": "insufficient process capacity"}, status=503)
    except (OSError, ValueError):
        pass  # cgroup v1 / an unconstrained local process has no v2 counter.
    return web.json_response({"ok": True})


def main() -> None:
    if not TOKEN or not PROXY:
        raise SystemExit("AILAB_CRAWLER_TOKEN and AILAB_CRAWLER_PROXY are required")
    app = web.Application(client_max_size=16 * 1024)
    app.router.add_post("/read", read)
    app.router.add_get("/healthz", health)
    web.run_app(app, host="0.0.0.0", port=PORT, access_log=None)


if __name__ == "__main__":
    main()
