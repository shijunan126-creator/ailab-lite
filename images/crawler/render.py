"""One disposable browser process. The supervisor owns its deadline and process tree."""

import asyncio
import contextlib
import ctypes
import json
import os
import re
import sys
import time

with contextlib.redirect_stdout(sys.stderr):
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
    from crawl4ai.content_filter_strategy import PruningContentFilter
    from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

PROXY = os.environ.get("AILAB_CRAWLER_PROXY", "")
PAGE_TIMEOUT_MS = int(os.environ.get("AILAB_CRAWLER_PAGE_TIMEOUT_MS", "20000"))
MAX_LINKS = 50
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
CHALLENGE = re.compile(
    r"^(?:just a moment[.!…]*|attention required[!！]?(?:\s*\|\s*cloudflare)?|access denied|verify (?:that )?you are human)[.!…\s]*$",
    re.I,
)
TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/json", "application/xml", "text/xml")


def reply(body, status=200):
    return {"status": status, "body": body}


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


async def render_page(url, limit, include_html=False):
    started = time.monotonic()
    crawler = AsyncWebCrawler(config=browser_config())
    try:
        await crawler.__aenter__()
        result = await crawler.arun(url, config=run_config())
    finally:
        await crawler.__aexit__(None, None, None)
    return extract_result(result, limit, started, include_html)


def extract_result(result, limit, started, include_html=False):
    # Crawl4AI success means the browser ran, including HTTP error pages from the proxy.
    if not result.success or (result.status_code or 0) >= 400:
        return reply(
            {
                "error": (result.error_message or f"HTTP {result.status_code} from {result.url}")[:300],
                "status": result.status_code,
            },
            status=502,
        )
    headers = {k.lower(): v for k, v in (result.response_headers or {}).items()}
    ctype = (headers.get("content-type") or "text/html").split(";")[0].strip().lower()
    if ctype not in TEXT_TYPES:
        return reply(
            {"error": f"unsupported content type: {ctype}; use download_url for files"},
            status=415,
        )
    markdown = result.markdown
    # The pruned view drops navigation and boilerplate; a page it empties (short or unusual layout) keeps the full text.
    text = (getattr(markdown, "fit_markdown", "") or "").strip() or (
        getattr(markdown, "raw_markdown", "") or (markdown if isinstance(markdown, str) else "")
    ).strip()
    title = str((result.metadata or {}).get("title") or "")[:300]
    if not text or CHALLENGE.fullmatch(title.strip()) or CHALLENGE.fullmatch(text.strip()):
        return reply(
            {"error": "page returned an access challenge or no readable text"},
            status=502,
        )
    snapshot = {}
    if include_html:
        html = str(getattr(result, "html", "") or "")
        if not html or len(html.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            return reply({"error": "rendered HTML is empty or exceeds the snapshot limit"}, status=502)
        snapshot = {"html": html}
    truncated = len(text) > limit
    if truncated:
        text = text[:limit].rstrip()
    return reply(
        {
            "url": result.redirected_url or result.url,
            "title": title,
            "status": result.status_code,
            "content_type": ctype,
            "markdown": text,
            "truncated": truncated,
            "links": links_of(result),
            "seconds": round(time.monotonic() - started, 2),
            **snapshot,
        }
    )


def main():
    # Keep double-forked Chrome/crashpad children in this request's subtree, even
    # before Playwright returns a handle. The supervisor can then stop/kill them all.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "could not become child subreaper")
    body = json.loads(sys.stdin.readline())
    with contextlib.redirect_stdout(sys.stderr):
        try:
            result = asyncio.run(render_page(body["url"], body["max_chars"], body.get("include_html", False)))
        except Exception as exc:
            result = reply({"error": f"render failed: {type(exc).__name__}"}, status=502)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    # Stay alive until the supervisor has captured and reaped the request tree.
    sys.stdin.read()


if __name__ == "__main__":
    main()
