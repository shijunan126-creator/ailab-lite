"""The side pane's original-page view: authenticated, public-web-only, bounded, error-mapped, script-free."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from ailab_platform import web
from ailab_platform.api.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture()
def api():
    with TestClient(app) as client:
        client.headers.update(AUTH)
        yield client


@pytest.fixture
def fake_html(monkeypatch):
    async def fetch(url):
        return {
            "url": url,
            "title": "T",
            "text": "<html><head><title>T</title></head><body><p>hello</p></body></html>",
            "content_type": "text/html",
            "truncated": False,
        }

    monkeypatch.setattr("ailab_platform.web.fetch_html", fetch)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")


def test_reader_render_serves_the_original_page(api, fake_html):
    r = api.get("/v1/reader/render", params={"url": "https://example.com/a"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<base href="https://example.com/a" target="_blank">' in r.text
    assert "hello" in r.text


def test_reader_render_refuses_non_html_pages(api, monkeypatch):
    async def fetch(url):
        return {"url": url, "title": "", "text": "just text", "content_type": "text/plain", "truncated": False}

    monkeypatch.setattr("ailab_platform.web.fetch_html", fetch)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    assert api.get("/v1/reader/render", params={"url": "https://example.com/a"}).status_code == 415


def test_reader_render_maps_fetch_failures_to_502(api, monkeypatch):
    async def fetch(url):
        raise web.WebError("HTTP 404 from https://example.com/gone")

    monkeypatch.setattr("ailab_platform.web.fetch_html", fetch)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    r = api.get("/v1/reader/render", params={"url": "https://example.com/gone"})
    assert r.status_code == 502
    assert "text/html" in r.headers["content-type"]
    assert "暂时无法预览" in r.text


def test_reader_render_needs_a_session(api, fake_html):
    assert (
        api.get("/v1/reader/render", params={"url": "https://example.com/a"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    )


def test_reader_render_refuses_non_http_schemes(api, fake_html):
    for url in ("ftp://example.com/a", "file:///etc/passwd", "javascript:alert(1)"):
        assert api.get("/v1/reader/render", params={"url": url}).status_code == 400


def test_reader_render_maps_a_stuck_page_to_504(api, monkeypatch):
    async def fetch(url):
        await asyncio.sleep(120)

    monkeypatch.setattr("ailab_platform.web.fetch_html", fetch)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    monkeypatch.setattr("ailab_platform.api.routes_reader.READ_TIMEOUT_SECONDS", 0.05)
    assert api.get("/v1/reader/render", params={"url": "https://example.com/slow"}).status_code == 504


def test_reader_render_is_off_when_web_access_is_off(api, fake_html, monkeypatch):
    monkeypatch.setattr(web.settings, "web_search_provider", "none")
    assert api.get("/v1/reader/render", params={"url": "https://example.com/a"}).status_code == 404


def test_render_snapshot_blocks_navigation_and_escapes_base(api, monkeypatch):
    async def fetch(url):
        return {
            "url": 'https://example.com/?q="x"&a=1',
            "content_type": "text/html",
            "text": '<html><head><BASE href="https://wrong.test"><meta HTTP-EQUIV="Refresh" content="0;url=/jump">'
            '</head><body><p>文章</p><script>location="/jump"</script></body></html>',
        }

    monkeypatch.setattr(web, "fetch_html", fetch)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    response = api.get("/v1/reader/render", params={"url": "https://example.com/"})
    assert response.status_code == 200
    assert "wrong.test" not in response.text and "HTTP-EQUIV" not in response.text
    assert 'href="https://example.com/?q=&quot;x&quot;&amp;a=1"' in response.text
    assert "script-src 'none'" in response.headers["content-security-policy"]
    assert "文章" in response.text


def test_render_preserves_doctype_and_its_standards_mode():
    from ailab_platform.api.routes_reader import _FrameMarkup

    for html in ("<!doctype html><html><head><title>T</title></head><body>正文</body></html>", "<!doctype html><p>正文</p>"):
        rendered = _FrameMarkup(html).render("https://example.com/")
        assert rendered.startswith("<!doctype html>")
        assert rendered.count("<base ") == 1


def test_render_handles_carriage_returns_and_valueless_meta_attributes():
    from ailab_platform.api.routes_reader import _FrameMarkup

    html = '<!doctype html>\r<html><head><meta http-equiv><base href="https://wrong.test"></head><body>正文</body></html>'
    rendered = _FrameMarkup(html).render("https://example.com/")
    assert rendered.startswith("<!doctype html>")
    assert "wrong.test" not in rendered
    assert rendered.count("<base ") == 1
    assert "正文" in rendered


@pytest.mark.parametrize("status", [403, 412])
def test_reader_retries_blocked_pages_once_in_isolated_browser(api, monkeypatch, status):
    calls = []

    async def fetch(url):
        raise web.WebStatusError(status, f"HTTP {status} from {url}")

    async def snapshot(url):
        calls.append(url)
        return {"url": url, "content_type": "text/html", "text": "<h1>浏览器正文</h1><script>bad()</script>"}

    monkeypatch.setattr(web, "fetch_html", fetch)
    monkeypatch.setattr(web, "fetch_rendered_html", snapshot)
    monkeypatch.setattr(web.settings, "crawler_url", "http://crawler:8090")
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    response = api.get("/v1/reader/render", params={"url": "https://example.com/a"})
    assert response.status_code == 200 and "浏览器正文" in response.text
    assert calls == ["https://example.com/a"]
    assert "script-src 'none'" in response.headers["content-security-policy"]


def test_reader_keeps_browser_refusal_as_failure_with_readable_html(api, monkeypatch):
    async def fetch(url):
        raise web.WebStatusError(403, f"HTTP 403 from {url}")

    monkeypatch.setattr(web, "fetch_html", fetch)
    monkeypatch.setattr(web, "fetch_rendered_html", fetch)
    monkeypatch.setattr(web.settings, "crawler_url", "http://crawler:8090")
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    response = api.get("/v1/reader/render", params={"url": "https://example.com/?q=<script>bad()</script>"})
    assert response.status_code == 502
    assert "网站拒绝" in response.text and "新标签" in response.text
    assert "<script>" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_reader_never_retries_private_address_or_tls_failure_in_browser(api, monkeypatch):
    async def fetch(url):
        raise web.WebError("refusing non-public address")

    async def forbidden(url):
        raise AssertionError("browser must not bypass public-only refusal")

    monkeypatch.setattr(web, "fetch_html", fetch)
    monkeypatch.setattr(web, "fetch_rendered_html", forbidden)
    monkeypatch.setattr(web.settings, "crawler_url", "http://crawler:8090")
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    assert api.get("/v1/reader/render", params={"url": "http://127.0.0.1/"}).status_code == 502
