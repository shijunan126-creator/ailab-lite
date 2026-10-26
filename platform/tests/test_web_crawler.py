"""read_url through the isolated crawler (ADR 0021): the Agent checks first, maps every crawler failure to a
defined tool error, and never falls back to its own fetch."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from ailab_platform import web
from ailab_platform.config import Settings

PAGE = {
    "url": "https://example.com/a",
    "title": "标题",
    "status": 200,
    "content_type": "text/html",
    "markdown": "# 标题\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n\n\n    code()\n",
    "truncated": False,
    "links": ["https://example.com/b", "javascript:alert(1)"],
}


@pytest.fixture
def crawler(monkeypatch):
    calls: list[dict] = []
    reply = {"status": 200, "json": PAGE}

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        if isinstance(reply.get("raise"), Exception):
            raise reply["raise"]
        return httpx.Response(reply["status"], json=reply["json"])

    async def public(host, port):
        if host in ("127.0.0.1", "internal.test"):
            raise web.WebError(f"refusing non-public address: {host}")
        return ["93.184.216.34"]

    monkeypatch.setattr(web.settings, "web_read_provider", "crawl4ai")
    monkeypatch.setattr(web.settings, "crawler_url", "http://platform-crawler:8090")
    monkeypatch.setattr(web.settings, "crawler_token", "t" * 32)
    monkeypatch.setattr(web, "public_addresses", public)
    monkeypatch.setattr(web, "_crawler_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handle)))

    async def no_static(url):
        raise AssertionError("crawl4ai mode must not fall back to the static fetch")

    monkeypatch.setattr(web, "fetch_html", no_static)
    return calls, reply


def test_rendered_page_keeps_markdown_structure_and_public_links(crawler):
    calls, _ = crawler
    page = asyncio.run(web.read_url("https://example.com/a", 5000))
    assert calls[0]["url"] == "http://platform-crawler:8090/read"
    assert calls[0]["auth"] == "Bearer " + "t" * 32
    assert calls[0]["body"] == {"url": "https://example.com/a", "max_chars": 5000}
    assert "| 1 | 2 |" in page["text"] and "    code()" in page["text"]  # tables and indentation survive
    assert "\n\n\n" not in page["text"]
    assert page["title"] == "标题" and page["links"] == ["https://example.com/b"]
    assert page["truncated"] is False


def test_rendered_page_is_truncated_to_the_requested_size(crawler):
    _, reply = crawler
    reply["json"] = {**PAGE, "markdown": "字" * 3000}
    page = asyncio.run(web.read_url("https://example.com/a", 1000))
    assert page["truncated"] and page["text"].endswith("...[truncated]")
    assert len(page["text"]) <= 1000 + len("\n...[truncated]")


def test_private_targets_are_refused_before_the_crawler_is_called(crawler):
    calls, _ = crawler
    for url in ("http://127.0.0.1/", "https://internal.test/", "http://example.com:8080/", "file:///etc/passwd"):
        with pytest.raises(web.WebError):
            asyncio.run(web.read_url(url))
    assert calls == []


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (503, {"error": "crawler is busy"}, "busy"),
        (504, {"error": "page did not finish loading"}, "did not finish"),
        (415, {"error": "unsupported content type: application/pdf; use download_url for files"}, "download_url"),
        (502, {}, "HTTP 502"),
        (200, {**PAGE, "markdown": "  "}, "no readable text"),
    ],
)
def test_crawler_failures_are_defined_tool_errors(crawler, status, body, message):
    _, reply = crawler
    reply["status"], reply["json"] = status, body
    with pytest.raises(web.WebError, match=message):
        asyncio.run(web.read_url("https://example.com/a"))


@pytest.mark.parametrize(("error", "message"), [(httpx.ReadTimeout("slow"), "timed out"), (httpx.ConnectError("down"), "unavailable")])
def test_unreachable_crawler_is_an_error_not_a_fallback(crawler, error, message):
    _, reply = crawler
    reply["raise"] = error
    with pytest.raises(web.WebError, match=message):
        asyncio.run(web.read_url("https://example.com/a"))


def test_github_issues_keep_their_api_path(crawler, monkeypatch):
    calls, _ = crawler

    async def issue(owner, repo, number, limit):
        return {"url": f"https://github.com/{owner}/{repo}/issues/{number}", "title": "issue", "text": "body", "truncated": False}

    monkeypatch.setattr(web, "read_github_issue", issue)
    assert asyncio.run(web.read_url("https://github.com/a/b/issues/1"))["title"] == "issue"
    assert calls == []


def test_unconfigured_crawler_is_refused(crawler, monkeypatch):
    calls, _ = crawler
    monkeypatch.setattr(web.settings, "crawler_url", "")
    with pytest.raises(web.WebError, match="not configured"):
        asyncio.run(web.read_url("https://example.com/a"))
    assert calls == []


def test_unknown_read_provider_is_rejected_by_settings():
    with pytest.raises(ValueError):
        Settings(web_read_provider="playwright")


def test_secure_profile_requires_crawler_url_and_a_real_token():
    settings = Settings(profile="secure", service_name="agent", web_read_provider="crawl4ai", crawler_url="", crawler_token="short")
    with pytest.raises(RuntimeError) as info:
        settings.validate_secrets("agent")
    assert "AILAB_CRAWLER_URL" in str(info.value) and "AILAB_CRAWLER_TOKEN" in str(info.value)


def test_crawler_token_is_redacted():
    assert Settings(crawler_token="x" * 32).redacted()["crawler_token"] == "<redacted>"
