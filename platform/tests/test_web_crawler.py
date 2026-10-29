"""read_url: httpx + Trafilatura first, the isolated crawler (ADR 0021) only when that comes back weak."""

from __future__ import annotations

import asyncio
import json
import ssl

import httpx
import pytest

from ailab_platform import web
from ailab_platform.config import Settings

PARAGRAPH = "这是一段足够长的正文内容，用来确认静态抓取就能读到文章本身，而不需要启动浏览器。" * 4
ARTICLE = f"""<html><head><title>站点标题</title></head><body>
<nav><a href="/menu">菜单</a><a href="/login">登录</a></nav>
<article><h1>文章标题</h1><p>{PARAGRAPH}</p><p>{PARAGRAPH}</p>
<table><tr><th>产品</th><th>时间</th></tr><tr><td>A</td><td>2026</td></tr></table>
<p><a href="https://example.com/next">下一篇</a></p></article>
<footer><p>版权所有 热线电话</p></footer></body></html>"""
SPA_SHELL = '<html><head><title>App</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
RENDERED = {
    "url": "https://example.com/a",
    "title": "渲染后标题",
    "status": 200,
    "content_type": "text/html",
    "markdown": "# 渲染后标题\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n\n\n    code()\n",
    "truncated": False,
    "links": ["https://example.com/b", "javascript:alert(1)"],
}


@pytest.fixture
def site(monkeypatch):
    state = {"html": ARTICLE, "fetch_error": None, "crawler": {"status": 200, "json": RENDERED}, "calls": []}

    async def fetch(url):
        if state["fetch_error"]:
            raise state["fetch_error"]
        return {"url": url, "title": "", "text": state["html"], "content_type": "text/html", "truncated": False}

    def crawler(request: httpx.Request) -> httpx.Response:
        state["calls"].append({"url": str(request.url), "auth": request.headers.get("authorization"), "body": json.loads(request.content)})
        reply = state["crawler"]
        if isinstance(reply, Exception):
            raise reply
        return httpx.Response(reply["status"], json=reply["json"])

    async def public(host, port):
        if host in ("127.0.0.1", "internal.test"):
            raise web.WebError(f"refusing non-public address: {host}")
        return ["93.184.216.34"]

    monkeypatch.setattr(web, "fetch_html", fetch)
    monkeypatch.setattr(web, "public_addresses", public)
    monkeypatch.setattr(web, "_crawler_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(crawler)))
    monkeypatch.setattr(web.settings, "crawler_url", "http://platform-crawler:8090")
    monkeypatch.setattr(web.settings, "crawler_token", "t" * 32)
    return state


def read(url="https://example.com/a", max_chars=8000):
    return asyncio.run(web.read_url(url, max_chars))


def test_a_normal_article_is_read_without_the_browser(site):
    page = read()
    assert site["calls"] == []
    assert "文章标题" in page["text"] and PARAGRAPH[:20] in page["text"]
    assert "版权所有" not in page["text"] and "登录" not in page["text"]  # chrome dropped by Trafilatura
    assert "产品" in page["text"]  # tables kept
    assert "https://example.com/next" in page["links"]
    assert "note" not in page


def test_an_app_shell_is_rendered_by_the_crawler(site):
    site["html"] = SPA_SHELL
    page = read(max_chars=5000)
    (call,) = site["calls"]
    assert call["url"] == "http://platform-crawler:8090/read"
    assert call["auth"] == "Bearer " + "t" * 32
    assert call["body"] == {"url": "https://example.com/a", "max_chars": 5000}
    assert "| 1 | 2 |" in page["text"] and "    code()" in page["text"] and "\n\n\n" not in page["text"]
    assert page["title"] == "渲染后标题" and page["links"] == ["https://example.com/b"]


@pytest.mark.parametrize("status", [403, 408, 412, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524])
def test_a_bot_wall_is_rendered_by_the_crawler(site, status):
    site["fetch_error"] = web.WebStatusError(status, f"HTTP {status} from https://example.com/a")
    assert read()["title"] == "渲染后标题"
    assert len(site["calls"]) == 1


def test_a_missing_page_is_an_error_without_rendering(site):
    site["fetch_error"] = web.WebStatusError(404, "HTTP 404 from https://example.com/a")
    with pytest.raises(web.WebError, match="404"):
        read()
    assert site["calls"] == []


def test_a_failed_render_returns_the_static_text_marked_partial(site):
    site["html"] = '<html><body><p>只有一句话的页面。</p><script src="/app.js"></script></body></html>'
    site["crawler"] = {"status": 504, "json": {"error": "page did not finish loading"}}
    page = read()
    assert "只有一句话的页面" in page["text"]
    assert "渲染失败" in page["note"]


def test_short_static_pages_without_scripts_do_not_need_rendering(site):
    site["html"] = "<html><body><p>只有一句话的页面。</p></body></html>"
    assert "只有一句话的页面" in read()["text"]
    assert site["calls"] == []


def test_transport_failure_tries_one_browser_connection(site):
    site["fetch_error"] = web.WebFetchError("connection reset")
    assert read()["title"] == "渲染后标题"
    assert len(site["calls"]) == 1


def test_static_challenge_is_not_returned_as_partial_content(site):
    site["html"] = "<html><head><title>Just a moment...</title></head><body>Checking your browser<script></script></body></html>"
    site["crawler"] = {"status": 502, "json": {"error": "access challenge"}}
    with pytest.raises(web.WebError, match="access challenge"):
        read()


@pytest.mark.parametrize("certificate_error", [False, True])
def test_fetch_transport_classification_preserves_tls_verification(monkeypatch, certificate_error):
    async def broken(request):
        if certificate_error:
            raise httpx.ConnectError("TLS failed") from ssl.SSLCertVerificationError("invalid certificate")
        raise httpx.RemoteProtocolError("peer closed connection")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
            async with web.open_public(client, "https://example.com/"):
                pytest.fail("unexpected response")

    expected = web.WebError if certificate_error else web.WebFetchError
    with pytest.raises(expected) as error:
        asyncio.run(run())
    if certificate_error:
        assert not isinstance(error.value, web.WebFetchError)


@pytest.mark.parametrize(
    "error", ["TLS certificate verification failed", "unsupported content type: application/pdf", "refusing non-public address"]
)
def test_permanent_or_unsafe_failures_do_not_start_a_browser(site, error):
    site["fetch_error"] = web.WebError(error)
    with pytest.raises(web.WebError, match=error):
        read()
    assert site["calls"] == []


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ({"status": 503, "json": {"error": "crawler is busy"}}, "busy"),
        ({"status": 504, "json": {"error": "page did not finish loading"}}, "did not finish"),
        ({"status": 415, "json": {"error": "unsupported content type: application/pdf; use download_url for files"}}, "download_url"),
        ({"status": 502, "json": {}}, "HTTP 502"),
        ({"status": 200, "json": {**RENDERED, "markdown": "  "}}, "no readable text"),
        (httpx.ReadTimeout("slow"), "timed out"),
        (httpx.ConnectError("down"), "unavailable"),
    ],
)
def test_crawler_failures_with_nothing_static_are_defined_errors(site, reply, message):
    site["html"] = SPA_SHELL
    site["crawler"] = reply
    with pytest.raises(web.WebError, match=message):
        read()


def test_without_a_crawler_weak_pages_return_what_the_fetch_found(site, monkeypatch):
    monkeypatch.setattr(web.settings, "crawler_url", "")
    site["html"] = "<html><body><p>简短页面。</p></body></html>"
    assert "简短页面" in read()["text"]
    site["html"] = SPA_SHELL
    with pytest.raises(web.WebError, match="no readable text"):
        read()
    assert site["calls"] == []


def test_rendered_text_is_truncated_to_the_requested_size(site):
    site["html"] = SPA_SHELL
    site["crawler"] = {"status": 200, "json": {**RENDERED, "markdown": "字" * 3000}}
    page = read(max_chars=1000)
    assert page["truncated"] and page["text"].endswith("...[truncated]")
    assert len(page["text"]) <= 1000 + len("\n...[truncated]")


def test_private_targets_are_refused_before_any_fetch(site):
    site["fetch_error"] = AssertionError("must not fetch")
    for url in ("http://127.0.0.1/", "http://example.com:8080/", "file:///etc/passwd"):
        with pytest.raises(web.WebError):
            read(url)
    site["fetch_error"] = web.WebStatusError(403, "HTTP 403")
    with pytest.raises(web.WebError, match="non-public"):
        read("https://internal.test/")  # resolves privately: the crawler is not asked either
    assert site["calls"] == []


def test_github_issues_keep_their_api_path(site, monkeypatch):
    async def issue(owner, repo, number, limit):
        return {"url": f"https://github.com/{owner}/{repo}/issues/{number}", "title": "issue", "text": "body", "truncated": False}

    monkeypatch.setattr(web, "read_github_issue", issue)
    site["fetch_error"] = AssertionError("must not fetch")
    assert read("https://github.com/a/b/issues/1")["title"] == "issue"
    assert site["calls"] == []


@pytest.mark.parametrize("service", ["agent", "api"])
def test_secure_profile_requires_a_real_crawler_token_only_when_rendering_is_on(service):
    problems = []
    for url in ("http://platform-crawler:8090", ""):
        settings = Settings(profile="secure", service_name=service, crawler_url=url, crawler_token="short")
        with pytest.raises(RuntimeError) as info:
            settings.validate_secrets(service)
        problems.append("AILAB_CRAWLER_TOKEN" in str(info.value))
    assert problems == [True, False]


def test_crawler_token_is_redacted():
    assert Settings(crawler_token="x" * 32).redacted()["crawler_token"] == "<redacted>"


def test_crawler_deadline_includes_a_trickling_response_and_closes_it(site, monkeypatch):
    closed = []

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            for part in (b'{"markdown": "article",', b'"title": "Title",', b'"links": [],', b'"status": 200}'):
                await asyncio.sleep(0.03)
                yield part

        async def aclose(self):
            closed.append(True)

    monkeypatch.setattr(web.settings, "crawler_timeout_seconds", 0.07)
    monkeypatch.setattr(
        web, "_crawler_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Trickle())))
    )
    with pytest.raises(web.WebError, match="timed out"):
        asyncio.run(web._read_rendered("https://example.com/", 4000))
    assert closed


def test_crawler_deadline_also_bounds_dns(site, monkeypatch):
    async def slow_dns(host, port):
        await asyncio.sleep(0.15)
        return ["93.184.216.34"]

    monkeypatch.setattr(web, "public_addresses", slow_dns)
    monkeypatch.setattr(web.settings, "crawler_timeout_seconds", 0.03)
    with pytest.raises(web.WebError, match="timed out"):
        asyncio.run(web._read_rendered("https://example.com/", 4000))


def test_html_snapshot_uses_same_authenticated_crawler_and_checks_final_destination(site):
    site["crawler"]["json"] = {**RENDERED, "html": "<h1>浏览器快照</h1>"}
    result = asyncio.run(web.fetch_rendered_html("https://example.com/a"))
    assert result["text"] == "<h1>浏览器快照</h1>"
    assert site["calls"][0]["body"]["include_html"] is True
    assert site["calls"][0]["auth"] == "Bearer " + "t" * 32
    site["crawler"]["json"]["url"] = "http://internal.test/"
    with pytest.raises(web.WebError, match="refusing"):
        asyncio.run(web.fetch_rendered_html("https://example.com/a"))


def test_html_snapshot_rejects_missing_and_oversized_html(site, monkeypatch):
    with pytest.raises(web.WebError, match="snapshot limit"):
        asyncio.run(web.fetch_rendered_html("https://example.com/a"))
    site["crawler"]["json"] = {**RENDERED, "html": "文" * 10}
    monkeypatch.setattr(web.settings, "web_max_bytes", 20)
    with pytest.raises(web.WebError, match="snapshot limit"):
        asyncio.run(web.fetch_rendered_html("https://example.com/a"))
