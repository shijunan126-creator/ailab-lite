"""The side pane's original-page view: authenticated, public-web-only, bounded, error-mapped, script-free."""

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
    assert "404" in r.json()["detail"]


def test_reader_render_needs_a_session(api, fake_html):
    assert (
        api.get("/v1/reader/render", params={"url": "https://example.com/a"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    )


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
