"""read_url without a browser: negotiation, structured-data recovery, Wayback and pagination."""

from __future__ import annotations

import asyncio

import pytest

from ailab_platform import web
from ailab_platform.config import Settings

ARTICLE = "这是一段足够长的正文内容，用来确认结构化数据和存档快照都能被读成文章。" * 8


def html_page(body: str, title: str = "Article") -> dict:
    return {
        "url": "https://example.com/a",
        "status": 200,
        "content_type": "text/html",
        "text": f"<html><head><title>{title}</title></head><body>{body}</body></html>",
        "truncated": False,
    }


def read(url: str = "https://example.com/a", max_chars: int | None = 8000, start: int = 0) -> dict:
    return asyncio.run(web.read_url(url, max_chars, start))


def test_a_blocked_page_becomes_a_wayback_snapshot(monkeypatch):
    calls: list[str] = []

    async def fetch(url, headers, deadline):
        calls.append(url)
        if url.startswith("https://web.archive.org/web/"):
            return {**html_page(f"<article><p>{ARTICLE}</p></article>", "Archived"), "url": url}
        return {"url": url, "status": 403, "content_type": "text/html", "text": "denied", "truncated": False}

    async def available(url, max_bytes, headers=None):
        return {"archived_snapshots": {"closest": {"available": True, "timestamp": "20240102030405"}}}

    monkeypatch.setattr(web, "_fetch_page", fetch)
    monkeypatch.setattr(web, "get_json", available)
    page = read()
    assert "正文内容" in page["text"]
    assert page["archived_at"] == "2024-01-02T03:04:05Z"
    assert "存档快照" in page["note"] and "可能不是最新" in page["note"]
    assert calls[-1] == "https://web.archive.org/web/20240102030405id_/https://example.com/a"


def test_a_challenge_page_also_uses_a_wayback_snapshot(monkeypatch):
    async def fetch(url, headers, deadline):
        if url.startswith("https://web.archive.org/"):
            return {**html_page(f"<article><p>{ARTICLE}</p></article>", "Archived"), "url": url}
        return {**html_page("<p>checking your browser</p>", "Just a moment..."), "url": url}

    async def available(url, max_bytes, headers=None):
        return {"archived_snapshots": {"closest": {"available": True, "timestamp": "20230101000000"}}}

    monkeypatch.setattr(web, "_fetch_page", fetch)
    monkeypatch.setattr(web, "get_json", available)
    assert read()["archived_at"] == "2023-01-01T00:00:00Z"


def test_no_snapshot_is_a_defined_error_that_tells_the_model_to_switch_sources(monkeypatch):
    async def fetch(url, headers, deadline):
        return {"url": url, "status": 429, "content_type": "text/html", "text": "slow down", "truncated": False}

    async def available(url, max_bytes, headers=None):
        return {"archived_snapshots": {}}

    monkeypatch.setattr(web, "_fetch_page", fetch)
    monkeypatch.setattr(web, "get_json", available)
    with pytest.raises(web.WebStatusError) as error:
        read()
    assert error.value.status == 429 and "换其他来源" in str(error.value)


def test_a_short_deadline_skips_the_archive_lookup(monkeypatch):
    async def fetch(url, headers, deadline):
        return {"url": url, "status": 403, "content_type": "text/html", "text": "denied", "truncated": False}

    async def available(url, max_bytes, headers=None):
        raise AssertionError("no time left for archive lookup")

    monkeypatch.setattr(web.settings, "web_read_deadline_seconds", 2.5)
    monkeypatch.setattr(web, "_fetch_page", fetch)
    monkeypatch.setattr(web, "get_json", available)
    with pytest.raises(web.WebStatusError, match="换其他来源"):
        read()


def test_json_ld_article_body_fills_a_script_shell(monkeypatch):
    ld = '{"@type":"NewsArticle","articleBody":"' + ARTICLE + '"}'

    async def fetch(url, headers, deadline):
        return html_page(f'<div id="root"></div><script type="application/ld+json">{ld}</script>')

    monkeypatch.setattr(web, "_fetch_page", fetch)
    page = read()
    assert ARTICLE[:40] in page["text"]
    assert "结构化数据" in page["note"]


def test_next_data_and_meta_description_rescue_script_shells(monkeypatch):
    next_data = '{"props":{"pageProps":{"content":"' + ARTICLE + '"}}}'

    async def with_next(url, headers, deadline):
        return html_page(f'<div id="root"></div><script id="__NEXT_DATA__" type="application/json">{next_data}</script>')

    monkeypatch.setattr(web, "_fetch_page", with_next)
    assert ARTICLE[:40] in read()["text"]

    description = "页面描述来自 meta 标签，长度足够时可以在没有正文的脚本空壳页里作为补救内容返回给模型。" * 2

    async def with_meta(url, headers, deadline):
        return html_page(f'<meta name="description" content="{description}"><div id="root"></div>')

    monkeypatch.setattr(web, "_fetch_page", with_meta)
    assert "页面描述来自 meta" in read()["text"]


def test_a_script_shell_without_salvageable_text_is_an_error(monkeypatch):
    async def fetch(url, headers, deadline):
        return html_page('<div id="root"></div><script src="/app.js"></script>')

    async def available(url, max_bytes, headers=None):
        return {"archived_snapshots": {}}

    monkeypatch.setattr(web, "_fetch_page", fetch)
    monkeypatch.setattr(web, "get_json", available)
    with pytest.raises(web.WebError, match="换其他来源"):
        read()


def test_text_is_a_character_window_with_next_start(monkeypatch):
    full = "".join(str(i % 10) for i in range(3500))
    calls: list[str] = []

    async def fetch(url, headers, deadline):
        calls.append(url)
        return {"url": url, "status": 200, "content_type": "text/markdown", "text": full, "truncated": False}

    monkeypatch.setattr(web, "_fetch_page", fetch)
    first = read(max_chars=1000)
    assert first["text"] == full[:1000]
    assert first["truncated"] is True and first["next_start"] == 1000
    second = read(max_chars=1000, start=first["next_start"])
    assert second["text"] == full[1000:2000]
    assert second["truncated"] is True and second["next_start"] == 2000
    tail = read(max_chars=1000, start=3000)
    assert tail["text"] == full[3000:3500]
    assert tail["truncated"] is False and "next_start" not in tail
    assert read(max_chars=1000, start=5000)["text"] == ""
    assert calls == ["https://example.com/a"] * 4


def test_read_deadline_is_bounded_configuration():
    assert Settings().web_read_deadline_seconds == 12
    with pytest.raises(ValueError):
        Settings(web_read_deadline_seconds=2)
    with pytest.raises(ValueError):
        Settings(web_read_deadline_seconds=121)


def test_resolve_pins_every_checked_address_in_one_entry_ipv4_first():
    """libcurl keeps only the last RESOLVE entry per host:port; separate entries pinned an unroutable IPv6 address."""
    from ailab_platform.web import _resolve_entries

    entries = _resolve_entries("example.com", 443, ["2606:4700::1", "104.26.13.77", "172.67.68.3"])
    assert entries == [b"example.com:443:104.26.13.77,172.67.68.3,[2606:4700::1]"]


def test_cancelled_reads_cannot_push_extraction_past_one_thread(monkeypatch):
    """A cancelled await leaves its thread running; the bound must hold for the threads, not for the awaiters."""
    import threading

    gate, lock = threading.Event(), threading.Lock()
    running = {"now": 0, "peak": 0}

    def slow_extract(html, base, recover=False):
        with lock:
            running["now"] += 1
            running["peak"] = max(running["peak"], running["now"])
        gate.wait(5)
        with lock:
            running["now"] -= 1
        return {}

    monkeypatch.setattr(web, "_extract_document", slow_extract)

    async def scenario():
        first = [asyncio.create_task(web._extract_bounded("<p>x</p>", "")) for _ in range(2)]
        await asyncio.sleep(0.05)
        for task in first:  # an excerpt deadline abandons both extractions while the first one's thread still runs
            task.cancel()
        await asyncio.gather(*first, return_exceptions=True)
        later = [asyncio.create_task(web._extract_bounded("<p>y</p>", "")) for _ in range(2)]
        await asyncio.sleep(0.2)
        peak = running["peak"]
        gate.set()
        await asyncio.gather(*later)
        return peak

    assert asyncio.run(scenario()) == 1


def test_extraction_returns_freed_heap_even_when_parsing_fails(monkeypatch):
    """A parsed page's freed chunks stay resident unless trimmed; resident memory then ratchets up to the agent's limit."""
    trims = []
    monkeypatch.setattr(web, "_MALLOC_TRIM", trims.append)

    def broken(html, base, recover=False):
        raise web.WebError("could not parse HTML: ValueError")

    monkeypatch.setattr(web, "_extract_document", broken)
    with pytest.raises(web.WebError):
        asyncio.run(web._extract_bounded("<p>x</p>", ""))
    assert trims == [0]


def test_page_title_comes_from_one_parse_without_metadata_date_search(monkeypatch):
    """extract_metadata's date search costs ~1.9 CPU-seconds and ~90 MiB on a 2 MiB page; only the title is used."""

    def forbidden(*args, **kwargs):
        raise AssertionError("extract_metadata parses the page a second time")

    monkeypatch.setattr(web.trafilatura, "extract_metadata", forbidden)
    body = "<article><h1>Headline</h1>" + "<p>Paragraph text that is long enough to keep as content.</p>" * 20 + "</article>"
    page = web._extract_document(
        f'<html><head><title>Site | Tab</title><meta property="og:title" content="Shared title"></head><body>{body}</body></html>',
        "https://example.com/a",
    )
    assert page["title"] == "Shared title"
    assert "Paragraph text" in page["text"]
    plain = web._extract_document(f"<html><head><title>Only tab title</title></head><body>{body}</body></html>", "https://example.com/b")
    assert plain["title"] == "Headline"
