"""The side-pane reader: authenticated, public-web-only, bounded, and error-mapped."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from ailab_platform.api.main import app

AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture()
def api():
    with TestClient(app) as client:
        client.headers.update(AUTH)
        yield client


@pytest.fixture
def fake_page(monkeypatch):
    async def read(url, max_chars=None):
        return {"url": url, "title": "页面标题", "text": "正文内容", "content_type": "text/html", "truncated": False}

    monkeypatch.setattr("ailab_platform.web.read_url", read)


def test_reader_needs_a_session(api, fake_page):
    assert api.get("/v1/reader", params={"url": "https://example.com/a"}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_reader_returns_the_extracted_page(api, fake_page):
    r = api.get("/v1/reader", params={"url": "https://example.com/a"})
    assert r.status_code == 200
    assert r.json() == {"url": "https://example.com/a", "title": "页面标题", "text": "正文内容", "truncated": False}


def test_reader_refuses_non_http_schemes(api, fake_page):
    for url in ("ftp://example.com/a", "file:///etc/passwd", "javascript:alert(1)"):
        assert api.get("/v1/reader", params={"url": url}).status_code == 400


def test_reader_maps_fetch_failures_to_502(api, monkeypatch):
    from ailab_platform import web

    async def read(url, max_chars=None):
        raise web.WebError("HTTP 404 from https://example.com/gone")

    monkeypatch.setattr("ailab_platform.web.read_url", read)
    r = api.get("/v1/reader", params={"url": "https://example.com/gone"})
    assert r.status_code == 502
    assert "404" in r.json()["detail"]


def test_reader_maps_a_stuck_page_to_504(api, monkeypatch):
    async def read(url, max_chars=None):
        await asyncio.sleep(120)

    monkeypatch.setattr("ailab_platform.web.read_url", read)
    monkeypatch.setattr("ailab_platform.api.routes_reader.READ_TIMEOUT_SECONDS", 0.05)
    assert api.get("/v1/reader", params={"url": "https://example.com/slow"}).status_code == 504
