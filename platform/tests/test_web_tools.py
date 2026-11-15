"""Agent-side web tools: supplemental provenance, per-run dedupe, segmented reads and excerpts."""

import asyncio
import time

import pytest
from pydantic import ValidationError

from ailab_platform import web
from ailab_platform.agent import tools, web_tools
from ailab_platform.config import Settings, settings

URL = "https://r.jina.ai/reader?u=1"


def _hit(url=URL, title="Reader", snippet="正文"):
    return {"title": title, "url": url, "snippet": snippet, "engine": "test"}


def _page(url=URL, text="body", next_start=None):
    return {"url": url, "title": "Reader", "text": text, "links": [], "truncated": next_start is not None, "next_start": next_start}


def test_a_url_outside_the_sources_is_read_after_one_supplemental_search(monkeypatch):
    searches, reads = [], []

    async def search(query, count=None, kind="web"):
        searches.append((query, count, kind))
        return [_hit(url=URL), _hit(url="https://other.example/x")]

    async def read(url, max_chars=None, start=0):
        reads.append((url, max_chars, start))
        return _page(url, next_start=4000)

    monkeypatch.setattr(web, "search", search)
    monkeypatch.setattr(web, "read_url", read)
    ctx = tools.ToolContext(messages=[{"role": "user", "content": "读一下 jina reader"}])
    ctx.step = 2
    result = asyncio.run(web_tools.read_page(ctx, URL + "#top", max_chars=2000))
    assert result["ok"] is True and result["via_search"] is True
    assert searches == [(URL + "#top", settings.web_max_results, "web")]
    assert reads == [(URL, 2000, 0)]  # the search result URL is the one actually read
    assert ctx.budget.requests == 2  # the search and the read
    assert ctx.web_reads[web_tools._read_key(URL, 0)] == {"step": 3, "next_start": 4000}


def test_a_search_that_does_not_contain_the_url_keeps_the_original_error(monkeypatch):
    async def search(*args, **kwargs):
        return [_hit(url="https://other.example/x", title="Other", snippet="别的")]

    async def never(*args, **kwargs):
        raise AssertionError("the refused URL must not be fetched")

    monkeypatch.setattr(web, "search", search)
    monkeypatch.setattr(web, "read_url", never)
    ctx = tools.ToolContext(messages=[{"role": "user", "content": "查资料"}])
    result = asyncio.run(web_tools.read_page(ctx, "https://evil.example/?d=secret"))
    assert result["ok"] is False and "did not come from" in result["error"]
    assert result["results"] == [{"title": "Other", "url": "https://other.example/x", "snippet": "别的"}]
    assert ctx.budget.requests == 1 and not ctx.web_reads


def test_a_repeated_segment_is_answered_from_the_transcript(monkeypatch):
    reads = []

    async def read(url, max_chars=None, start=0):
        reads.append((url, start))
        return _page(url, next_start=8000)

    monkeypatch.setattr(web, "read_url", read)
    ctx = tools.ToolContext(messages=[{"role": "user", "content": URL}])
    ctx.step = 0
    first = asyncio.run(web_tools.read_page(ctx, URL))
    assert first["next_start"] == 8000
    again = asyncio.run(web_tools.read_page(ctx, URL + "#section"))
    assert again == {
        "ok": True,
        "url": URL,
        "duplicate": True,
        "note": "第 1 步已读过这一段，内容见上文；要后续内容请用 start=8000",
    }
    assert reads == [(URL, 0)]  # no second fetch
    onwards = asyncio.run(web_tools.read_page(ctx, URL, start=8000))
    assert onwards["ok"] is True and reads == [(URL, 0), (URL, 8000)]


def test_start_must_be_a_non_negative_integer():
    ctx = tools.ToolContext(messages=[{"role": "user", "content": URL}])
    for bad in (-1, True, "8000"):
        with pytest.raises(tools.ToolError, match="start"):
            asyncio.run(tools.execute_tool("read_url", {"url": URL, "start": bad}, "", None, ctx))


def test_search_results_carry_bounded_excerpts_from_the_top_three(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "searxng")
    hits = [_hit(url=f"https://example.com/{i}", title=f"Hit {i}") for i in range(4)]
    pages = {
        "https://example.com/0": "导航\n\n猫的饲养需要清水。\n\n无关段落。",
        "https://example.com/1": "无关\n\n猫需要定期打疫苗。",
        "https://example.com/2": "广告",
    }
    reads = []

    async def search(query, count=None, kind="web"):
        return hits

    async def read(url, max_chars=None):
        reads.append(url)
        if url.endswith("/2"):
            raise web.WebError("page unavailable")
        return _page(url, text=pages[url])

    monkeypatch.setattr(web, "search", search)
    monkeypatch.setattr(web, "read_url", read)
    ctx = tools.ToolContext(messages=[{"role": "user", "content": "猫"}])
    found = asyncio.run(web_tools.search(ctx, "猫 疫苗", 4, "web"))
    assert [hit.get("excerpt") for hit in found] == ["猫的饲养需要清水。", "猫需要定期打疫苗。", None, None]
    assert reads == ["https://example.com/0", "https://example.com/1", "https://example.com/2"]
    assert ctx.budget.requests == 4  # one search, three page reads
    assert all(len(hit.get("excerpt") or "") <= 800 for hit in found)


def test_a_slow_excerpt_read_does_not_hold_the_search(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "searxng")
    monkeypatch.setattr(settings, "web_excerpt_deadline_seconds", 0.05)
    hits = [_hit(url=f"https://example.com/{i}", title=f"Hit {i}") for i in range(3)]

    async def search(query, count=None, kind="web"):
        return hits

    async def read(url, max_chars=None):
        if url.endswith("/2"):
            await asyncio.sleep(0.4)
        return _page(url, text="猫的正文。")

    monkeypatch.setattr(web, "search", search)
    monkeypatch.setattr(web, "read_url", read)
    ctx = tools.ToolContext(messages=[{"role": "user", "content": "猫"}])
    started = time.monotonic()
    found = asyncio.run(web_tools.search(ctx, "猫", 3, "web"))
    assert time.monotonic() - started < 0.3
    assert [hit.get("excerpt") for hit in found] == ["猫的正文。", "猫的正文。", None]


def test_mock_search_results_never_fetch_pages(monkeypatch):
    monkeypatch.setattr(settings, "web_search_provider", "mock")

    async def never(*args, **kwargs):
        raise AssertionError("mock search results have no real page to read")

    monkeypatch.setattr(web, "read_url", never)
    ctx = tools.ToolContext(messages=[{"role": "user", "content": "猫"}])
    found = asyncio.run(web_tools.search(ctx, "猫", 3, "web"))
    assert found and all("excerpt" not in hit for hit in found)


def test_the_excerpt_deadline_is_range_checked():
    with pytest.raises(ValidationError):
        Settings(profile="dev", service_name="agent", web_excerpt_deadline_seconds=0)
    with pytest.raises(ValidationError):
        Settings(profile="dev", service_name="agent", web_excerpt_deadline_seconds=31)
