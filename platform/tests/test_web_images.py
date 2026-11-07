"""Web pictures in answers: the model only gets signed same-origin references, and the API proxies them safely."""

import asyncio
import json
import uuid
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ailab_platform import web, web_images
from ailab_platform.agent import tools
from ailab_platform.agent.loop import web_sources
from ailab_platform.api import routes_web_images
from ailab_platform.api.main import app
from ailab_platform.config import Settings, settings

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


def token_of(path: str) -> str:
    assert path.startswith("/v1/web-images/")
    return path.rsplit("/", 1)[1]


# ---------------------------------------------------------------- signing


def test_reference_round_trips_and_rejects_tampering_and_other_keys(monkeypatch):
    url = "https://img.example.org/a/b.jpg?x=1"
    token = token_of(web_images.reference(url))
    assert web_images.verify(token) == url
    body, mac = token.split(".")
    other = token_of(web_images.reference("https://evil.example/?d=secret")).split(".")[0]
    for forged in (f"{other}.{mac}", f"{body}.{'A' * len(mac)}", f"{body}.{mac[:-1]}", body, f"{body}.{mac}.x", "", "%%%.abc"):
        assert web_images.verify(forged) is None
    monkeypatch.setattr(settings, "web_image_key", SecretStr("k" * 40))
    assert web_images.verify(token) is None  # signed with another key
    assert web_images.verify(token_of(web_images.reference(url))) == url


def test_secure_profile_never_signs_with_the_published_key(monkeypatch):
    monkeypatch.setattr(settings, "web_image_key", SecretStr(""))
    token = token_of(web_images.reference("https://img.example.org/a.jpg"))
    monkeypatch.setattr(settings, "profile", "secure")
    assert web_images.reference("https://img.example.org/a.jpg") is None
    assert web_images.verify(token) is None


@pytest.mark.parametrize("service", ["agent", "api"])
def test_secure_profile_requires_a_web_image_key_when_search_is_on(service):
    def problems(**overrides):
        try:
            Settings(profile="secure", service_name=service, **overrides).validate_secrets(service)
        except RuntimeError as exc:
            return str(exc)
        return ""

    assert "AILAB_WEB_IMAGE_KEY" in problems(web_search_provider="searxng", web_image_key="short")
    assert "AILAB_WEB_IMAGE_KEY" not in problems(web_search_provider="searxng", web_image_key="k" * 32)
    assert "AILAB_WEB_IMAGE_KEY" not in problems(web_search_provider="none")
    assert Settings(web_image_key="k" * 32).redacted()["web_image_key"] == "<redacted>"


def test_sniff_accepts_only_raster_images():
    assert web_images.sniff(JPEG) == "image/jpeg"
    assert web_images.sniff(PNG) == "image/png"
    assert web_images.sniff(b"GIF89a....") == "image/gif"
    assert web_images.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert web_images.sniff(b"\x00\x00\x00\x1cftypavif\x00\x00\x00\x00avifmif1") == "image/avif"
    assert web_images.sniff(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic") is None
    for other in (b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"<!doctype html><html>", b"", b"%PDF-1.7"):
        assert web_images.sniff(other) is None


# ---------------------------------------------------------------- image search

SEARXNG_IMAGES = {
    "results": [
        {
            "url": "https://news.example.com/story",
            "title": "Skyline",
            "img_src": "https://cdn.example.com/skyline.jpg",
            "thumbnail_src": "https://tse.example.net/th?id=1",
            "resolution": "1920 x 1080",
            "img_format": "jpeg",
            "engine": "bing images",
        },
        {"url": "https://a.example.com/", "title": "Vector", "img_src": "https://a.example.com/chart.svg", "img_format": "svg+xml"},
        {"url": "https://b.example.com/", "title": "Inline", "img_src": "data:image/png;base64,AAAA"},
        {"url": "https://c.example.com/p", "title": "Thumb only", "img_src": "", "thumbnail_src": "//thumbs.example.com/t.png"},
        {"url": "https://d.example.com/", "title": "Private", "img_src": "http://127.0.0.1/x.jpg"},
        {"url": "https://e.example.com/", "title": "Dup", "img_src": "https://cdn.example.com/skyline.jpg"},
    ]
}


def test_image_search_returns_signed_references_and_skips_unsafe_pictures(monkeypatch):
    seen = {}

    def reply(request):
        seen.update(request.url.params)
        return httpx.Response(200, json=SEARXNG_IMAGES)

    monkeypatch.setattr(web.settings, "web_search_provider", "searxng")
    original = httpx.AsyncClient
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(reply), **kwargs))
    results = asyncio.run(web.search("skyline", 8, "images"))
    assert seen["categories"] == "images"
    assert [r["title"] for r in results] == ["Skyline", "Thumb only"]
    first, second = results
    assert web_images.verify(token_of(first["image"])) == "https://cdn.example.com/skyline.jpg"
    assert (first["page_url"], first["width"], first["height"]) == ("https://news.example.com/story", 1920, 1080)
    assert web_images.verify(token_of(second["image"])) == "https://thumbs.example.com/t.png"
    assert "width" not in second
    # The raw picture URL never reaches the model; only the page showing it does.
    assert "cdn.example.com" not in json.dumps(results)


def test_mock_image_search_is_deterministic_and_the_tool_offers_kind(monkeypatch):
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    ctx = tools.ToolContext(messages=[{"role": "user", "content": "找图"}])
    args = {"query": "猫", "kind": "images", "max_results": 2}
    first = asyncio.run(tools.execute_tool("web_search", args, "", None, ctx))
    again = asyncio.run(tools.execute_tool("web_search", args, "", None, ctx))
    assert first == again and len(first["results"]) == 2
    assert all(r["image"].startswith("/v1/web-images/") and r["page_url"].startswith("https://") for r in first["results"])
    schema = tools.WEB_SEARCH["function"]["parameters"]["properties"]["kind"]
    assert schema == {"type": "string", "enum": ["web", "images"], "default": "web"}
    plain = asyncio.run(tools.execute_tool("web_search", {"query": "猫"}, "", None, ctx))
    assert all("url" in r and "image" not in r for r in plain["results"])


def test_image_hits_cite_their_page_not_the_picture():
    result = {"results": [{"title": "Skyline", "image": "/v1/web-images/abc.def", "page_url": "https://news.example.com/story"}]}
    assert web_sources(result) == [{"title": "Skyline", "url": "https://news.example.com/story", "site": "news.example.com"}]


# ---------------------------------------------------------------- read_url pictures

PAGE = """<html><head><title>Story</title>
<meta property="og:image" content="/media/cover.jpg"><meta property="og:image:alt" content="Cover">
<meta name="twitter:image" content="https://cdn.example.com/cover.jpg">
</head><body><article><h1>Story</h1><p>{body}</p>
<img src="/static/logo.png" alt="Site"><img src="https://t.example.com/p.gif" width="1" height="1">
<img src="photo-1.jpg" alt="First"><img src="data:image/gif;base64,R0lG" data-src="/lazy/photo-2.webp" alt="Second">
<img src="/a/drawing.svg"><img src="/media/cover.jpg"><img src="/icons/share.png" width="200">
<img src="/p/3.jpg"><img src="/p/4.jpg"><img src="/p/5.jpg"><img src="/p/6.jpg"><img src="/p/7.jpg">
</article></body></html>"""


def test_read_url_returns_page_pictures_as_signed_references(monkeypatch):
    html = PAGE.format(body="正文内容。" * 80)

    async def fetch(url):
        return {"url": "https://www.example.com/news/story", "title": "", "text": html, "content_type": "text/html", "truncated": False}

    monkeypatch.setattr(web, "fetch_html", fetch)
    monkeypatch.setattr(web.settings, "crawler_url", "")
    page = asyncio.run(web.read_url("https://www.example.com/news/story"))
    urls = [web_images.verify(token_of(item["image"])) for item in page["images"]]
    assert urls == [
        "https://www.example.com/media/cover.jpg",
        "https://cdn.example.com/cover.jpg",
        "https://www.example.com/news/photo-1.jpg",
        "https://www.example.com/lazy/photo-2.webp",
        "https://www.example.com/p/3.jpg",
        "https://www.example.com/p/4.jpg",
    ]
    assert [item["alt"] for item in page["images"][:4]] == ["Cover", "Cover", "First", "Second"]


# ---------------------------------------------------------------- proxy endpoint


@pytest.fixture()
def anonymous():
    from ailab_platform.api.derived_assets import backfill_batch
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import DerivedBudget
    from ailab_platform.storage import get_storage

    with TestClient(app) as client:
        with SessionLocal() as db:
            for _ in range(100):
                backfill_batch(db, get_storage())
                db.commit()
                if db.get(DerivedBudget, 1, populate_existing=True).backfill_complete:
                    break
        yield client  # no Authorization header: the signed token is the capability


@pytest.fixture()
def upstream(monkeypatch):
    """The public internet, as the proxy's public-only client sees it."""
    state = {"body": JPEG, "type": "image/jpeg", "requests": [], "status": 200}

    def reply(request):
        state["requests"].append(request)
        return httpx.Response(state["status"], content=state["body"], headers={"content-type": state["type"]})

    def client(timeout, headers=None):
        return httpx.AsyncClient(transport=httpx.MockTransport(reply), headers=headers, follow_redirects=False)

    monkeypatch.setattr(web, "public_client", client)
    return state


def picture(name="p.jpg"):
    url = f"https://img.example.com/{uuid.uuid4().hex}/{name}"
    return url, web_images.reference(url)


def test_proxy_fetches_once_then_serves_the_stored_copy(anonymous, upstream):
    url, path = picture()
    first = anonymous.get(path)
    assert first.status_code == 200 and first.content == JPEG
    assert first.headers["content-type"] == "image/jpeg"
    assert first.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert first.headers["content-security-policy"].startswith("default-src 'none'")
    assert first.headers["content-disposition"] == "inline"
    sent = upstream["requests"][0]
    assert str(sent.url) == url and sent.headers["referer"] == "https://img.example.com/" and "cookie" not in sent.headers
    assert sent.headers["accept"].startswith("image/")

    upstream["body"] = PNG  # the site changed; the answer keeps the picture it showed
    second = anonymous.get(path)
    assert second.status_code == 200 and second.content == JPEG
    assert len(upstream["requests"]) == 1
    assert second.headers["etag"] == first.headers["etag"]
    assert anonymous.get(path, headers={"If-None-Match": first.headers["etag"]}).status_code == 304
    assert len(upstream["requests"]) == 1


def test_proxy_refuses_forged_tokens_without_fetching(anonymous, upstream):
    _, path = picture()
    body, mac = token_of(path).split(".")
    forged = token_of(web_images.reference("https://evil.example/x.jpg")).split(".")[0]
    for bad in (f"{forged}.{mac}", f"{body}.{'A' * len(mac)}", "nope"):
        assert anonymous.get(f"/v1/web-images/{bad}").status_code == 404
    assert upstream["requests"] == []


@pytest.mark.parametrize(
    "body,ctype",
    [
        (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", "image/svg+xml"),
        (b"<!doctype html><title>x</title>", "image/jpeg"),
    ],
)
def test_proxy_refuses_what_is_not_a_raster_image(anonymous, upstream, body, ctype):
    upstream["body"], upstream["type"] = body, ctype
    response = anonymous.get(picture()[1])
    assert response.status_code == 415
    assert response.headers["cache-control"] == "no-store"
    assert body not in response.content


def test_proxy_bounds_size_and_maps_upstream_failures(anonymous, upstream, monkeypatch):
    monkeypatch.setattr(routes_web_images, "MAX_BYTES", 1000)
    upstream["body"] = JPEG + b"\x00" * 2000
    assert anonymous.get(picture()[1]).status_code == 413
    upstream["body"], upstream["status"] = JPEG, 404
    failed = anonymous.get(picture()[1])
    assert failed.status_code == 502 and failed.headers["cache-control"] == "no-store"
    assert anonymous.get(web_images.reference("http://10.0.0.5/inside.jpg")).status_code == 502
    assert len(upstream["requests"]) == 2  # the private address was refused before any connection


def test_mock_pictures_are_served_without_a_network(anonymous, upstream, monkeypatch):
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    hit = asyncio.run(web.search("猫", 1, "images"))[0]
    response = anonymous.get(hit["image"])
    assert response.status_code == 200 and web_images.sniff(response.content) == "image/png"
    assert upstream["requests"] == []


def test_ownerless_picture_keys_are_out_of_reach_of_service_grants():
    from ailab_platform.api.routes_derived_assets import authorize
    from ailab_platform.db import SessionLocal

    class Body:
        key = web_images.cache_key("https://img.example.com/x.jpg")
        version = "web"

    with SessionLocal() as db, pytest.raises(HTTPException) as info:
        authorize(db, Body(), None, None)
    assert info.value.status_code == 403


def test_api_and_agent_receive_the_same_optional_key_secret():
    path = Path(__file__).resolve().parents[1] / "deploy/base/apps/services.yaml"
    deployments = {doc["metadata"]["name"]: doc for doc in yaml.safe_load_all(path.read_text()) if doc and doc["kind"] == "Deployment"}
    for name in ("platform-api", "platform-agent"):
        container = deployments[name]["spec"]["template"]["spec"]["containers"][0]
        assert {"secretRef": {"name": "platform-web-image-key", "optional": True}} in container["envFrom"]
