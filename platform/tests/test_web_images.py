"""Web pictures in answers: the model only gets signed same-origin references, and the API proxies them safely."""

import asyncio
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


def test_new_images_do_not_require_a_signing_key():
    try:
        Settings(profile="secure", service_name="api", web_search_provider="searxng").validate_secrets("api")
    except RuntimeError as exc:
        assert "AILAB_WEB_IMAGE_KEY" not in str(exc)


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
    assert first["image"] == "https://cdn.example.com/skyline.jpg"
    assert (first["page_url"], first["width"], first["height"]) == ("https://news.example.com/story", 1920, 1080)
    assert second["image"] == "https://thumbs.example.com/t.png"
    assert "width" not in second
    # The raw picture URL never reaches the model; only the page showing it does.
    assert first["thumbnail_url"] == "https://tse.example.net/th?id=1"


def test_mock_image_search_is_deterministic_and_the_tool_offers_kind(monkeypatch):
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")

    class Platform:
        async def register_web_images(self, run_id, images):
            return {"images": [{"image": "img_AbCdEf123456", "page_url": i["page_url"]} for i in images]}

    ctx = tools.ToolContext(run_id="run", platform=Platform(), messages=[{"role": "user", "content": "找图"}])
    args = {"query": "猫", "kind": "images", "max_results": 2}
    first = asyncio.run(tools.execute_tool("web_search", args, "", None, ctx))
    again = asyncio.run(tools.execute_tool("web_search", args, "", None, ctx))
    assert first == again and len(first["results"]) == 2
    assert all(r["image"].startswith("img_") and r["page_url"].startswith("https://") for r in first["results"])
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
    urls = [item["image"] for item in page["images"]]
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


AUTH = {"Authorization": "Bearer test-token"}


@pytest.fixture()
def client():
    with TestClient(app, headers=AUTH) as client:
        yield client


@pytest.fixture()
def upstream(monkeypatch):
    """The public internet, as the proxy's public-only client sees it."""
    state = {"body": JPEG, "type": "image/jpeg", "requests": [], "status": 200}

    def reply(request):
        state["requests"].append(request)
        return httpx.Response(state["status"], stream=httpx.ByteStream(state["body"]), headers={"content-type": state["type"]})

    def client(timeout, headers=None):
        return httpx.AsyncClient(transport=httpx.MockTransport(reply), headers=headers, follow_redirects=False)

    monkeypatch.setattr(web, "public_client", client)
    return state


def picture(name="p.jpg"):
    url = f"https://img.example.com/{uuid.uuid4().hex}/{name}"
    return url, web_images.reference(url)


def register_picture(client, *, thumbnail="", url=None):
    from tests.run_claims import attempt_headers

    run = client.post("/v1/runs", json={"prompt": "图片"}).json()
    headers = attempt_headers(client, run["id"])
    url = url or f"https://img.example.com/{uuid.uuid4().hex}.jpg"
    payload = {"images": [{"image": url, "thumbnail_url": thumbnail, "page_url": "https://news.example.com/story", "title": "Picture"}]}
    response = client.post(f"/internal/runs/{run['id']}/web-images", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    image_id = response.json()["images"][0]["image"]
    return run, image_id, headers, payload


def test_proxy_streams_without_storage_and_reloads_from_origin(client, upstream, monkeypatch):
    from ailab_platform.storage import get_storage

    def forbidden(*args, **kwargs):
        raise AssertionError("web images must not write storage")

    monkeypatch.setattr(get_storage(), "put_bytes", forbidden)
    run, image_id, headers, payload = register_picture(client)
    path = f"/v1/web-images/{image_id}"
    first = client.get(path)
    assert first.status_code == 200 and first.content == JPEG
    assert first.headers["cache-control"] == "private, max-age=300"
    assert first.headers["x-content-type-options"] == "nosniff"
    assert "cookie" not in upstream["requests"][0].headers
    upstream["body"] = PNG
    assert client.get(path).content == PNG
    assert len(upstream["requests"]) == 2
    assert client.get(path + "/meta").json()["source"] == "https://news.example.com/story"
    assert client.post(f"/internal/runs/{run['id']}/web-images", json=payload, headers=headers).json()["images"][0]["image"] == image_id
    assert (
        client.post(f"/internal/runs/{run['id']}/web-images", json=payload, headers={"X-Service-Token": "test-service"}).status_code == 409
    )


def test_proxy_rejects_missing_auth_and_forged_refs_before_fetch(client, upstream):
    _, image_id, _, _ = register_picture(client)
    for token in ("img_XXXXXXXXXXXX", image_id[:-1], "nope"):
        assert client.get(f"/v1/web-images/{token}").status_code == 404
    with TestClient(app) as anonymous:
        assert anonymous.get(f"/v1/web-images/{image_id}").status_code == 401
    assert not upstream["requests"]


@pytest.mark.parametrize("body", [b"<svg>bad</svg>", b"<!doctype html>bad"])
def test_proxy_refuses_non_raster(client, upstream, body):
    upstream["body"] = body
    _, image_id, _, _ = register_picture(client)
    assert client.get(f"/v1/web-images/{image_id}").status_code == 502


def test_original_rejection_falls_back_to_thumbnail(client, monkeypatch):
    requests = []

    def reply(request):
        requests.append(str(request.url))
        if request.url.host == "big.example.com":
            return httpx.Response(200, headers={"content-length": "99999999"}, stream=httpx.ByteStream(b""))
        return httpx.Response(200, stream=httpx.ByteStream(PNG))

    monkeypatch.setattr(web, "public_client", lambda timeout, headers: httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    _, image_id, _, _ = register_picture(client, thumbnail="https://thumb.example.com/t.png", url="https://big.example.com/a.jpg")
    path = f"/v1/web-images/{image_id}"
    assert client.get(path).content == PNG
    assert requests == ["https://thumb.example.com/t.png"]
    requests.clear()
    assert client.get(path + "?variant=original").content == PNG
    assert requests == ["https://big.example.com/a.jpg", "https://thumb.example.com/t.png"]


def test_mock_picture_uses_registered_id_without_network(client, upstream, monkeypatch):
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    _, image_id, _, _ = register_picture(client, url=web.MOCK_IMAGE_BASE + "1.png")
    response = client.get(f"/v1/web-images/{image_id}")
    assert response.status_code == 200 and web_images.sniff(response.content) == "image/png"
    assert not upstream["requests"]


def test_ownerless_picture_keys_are_out_of_reach_of_service_grants():
    from ailab_platform.api.routes_derived_assets import authorize
    from ailab_platform.db import SessionLocal

    class Body:
        key = web_images.cache_key("https://img.example.com/x.jpg")
        version = "web"

    with SessionLocal() as db, pytest.raises(HTTPException) as info:
        authorize(db, Body(), None, None)
    assert info.value.status_code == 403


def test_only_api_keeps_optional_legacy_key_secret():
    path = Path(__file__).resolve().parents[1] / "deploy/base/apps/services.yaml"
    deployments = {doc["metadata"]["name"]: doc for doc in yaml.safe_load_all(path.read_text()) if doc and doc["kind"] == "Deployment"}
    for name in ("platform-api",):
        container = deployments[name]["spec"]["template"]["spec"]["containers"][0]
        assert {"secretRef": {"name": "platform-web-image-key", "optional": True}} in container["envFrom"]


def test_candidate_expiry_retention_and_fork_reference_survival(client, upstream):
    from datetime import timedelta

    from sqlalchemy import update

    from ailab_platform.api.web_image_refs import collect, copy_refs, release_runs, retain
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import Run, WebImage, WebImageRef, utcnow

    run, image_id, _, _ = register_picture(client)
    other, unused_id, _, _ = register_picture(client)
    old = utcnow() - timedelta(days=2)
    with SessionLocal() as db:
        original = db.get(Run, run["id"])
        original.status = "succeeded"
        retain(db, original, f'Image("{image_id}")')
        fork = Run(user_id=original.user_id, prompt="fork", status="succeeded")
        db.add(fork)
        db.flush()
        copy_refs(db, original, fork)
        fork_id = fork.id
        db.execute(update(WebImage).where(WebImage.id.in_([image_id, unused_id])).values(expires_at=old))
        db.execute(update(WebImageRef).where(WebImageRef.image_id == unused_id).values(expires_at=old))
        db.execute(update(Run).where(Run.id == other["id"]).values(status="succeeded"))
        db.commit()
        collect(db, 100)
        db.commit()
        assert db.get(WebImage, unused_id) is None
        assert db.get(WebImage, image_id) is not None
        release_runs(db, [original.id])
        db.commit()
        collect(db, 100)
        db.commit()
        assert db.get(WebImageRef, (fork_id, image_id)) is not None
        assert db.get(WebImage, image_id) is not None
        release_runs(db, [fork_id])
        db.commit()
        collect(db, 100)
        db.commit()
        assert db.get(WebImage, image_id) is None


def test_active_candidate_is_not_expired_and_collection_is_bounded(client):
    from datetime import timedelta

    from sqlalchemy import update

    from ailab_platform.api.web_image_refs import collect
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import WebImage, WebImageRef, utcnow

    _, image_id, _, _ = register_picture(client)
    with SessionLocal() as db:
        old = utcnow() - timedelta(days=2)
        db.execute(update(WebImage).where(WebImage.id == image_id).values(expires_at=old))
        db.execute(update(WebImageRef).where(WebImageRef.image_id == image_id).values(expires_at=old))
        db.commit()
        assert collect(db, 1) <= 1
        db.commit()
        assert db.get(WebImage, image_id) is not None


def test_owner_share_branch_and_revocation_permissions(client, upstream):
    from tests.run_claims import attempt_headers
    from tests.test_conversations import _finish, _other_user

    recipient = _other_user(client)
    conv = client.post("/v1/conversations", json={}).json()["id"]
    run = client.post(f"/v1/conversations/{conv}/messages", json={"prompt": "配图"}).json()
    headers = attempt_headers(client, run["id"])
    payload = {"images": [{"image": f"https://img.example.com/{uuid.uuid4().hex}.png", "page_url": "https://news.example.com/story"}]}
    image_id = client.post(f"/internal/runs/{run['id']}/web-images", headers=headers, json=payload).json()["images"][0]["image"]
    _finish(client, run["id"], f'```openui\nroot = Image("{image_id}")\n```')
    path = f"/v1/web-images/{image_id}"
    assert client.get(path, headers=recipient).status_code == 404
    share = client.post("/v1/shares", json={"kind": "conversation", "conversation_id": conv, "audience": "everyone"})
    assert share.status_code == 201, share.text
    sid = share.json()["id"]
    assert client.get(path + f"?share_id={sid}", headers=recipient).status_code == 200
    assert client.get(path + f"/meta?share_id={sid}", headers=recipient).status_code == 200
    later = client.post(f"/v1/conversations/{conv}/messages", json={"prompt": "下一张"}).json()
    later_headers = attempt_headers(client, later["id"])
    payload["images"][0]["image"] += "?later=1"
    later_id = client.post(f"/internal/runs/{later['id']}/web-images", headers=later_headers, json=payload).json()["images"][0]["image"]
    _finish(client, later["id"], f'Image("{later_id}")')
    assert client.get(f"/v1/web-images/{later_id}?share_id={sid}", headers=recipient).status_code == 404
    assert client.delete(f"/v1/shares/{sid}").status_code == 204
    assert client.get(path + f"?share_id={sid}", headers=recipient).status_code == 404
    assert client.delete(f"/v1/conversations/{conv}").status_code == 204
    assert client.get(path).status_code == 404


def test_transfer_is_incremental_and_resources_close_on_disconnect(monkeypatch):
    from ailab_platform.api.routes_web_images import open_image

    state = {"chunks": 0, "closed": False}

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(100):
                state["chunks"] += 1
                yield (JPEG if i == 0 else b"x") + b"x" * (65536 - (len(JPEG) if i == 0 else 1))

        async def aclose(self):
            state["closed"] = True

    monkeypatch.setattr(
        web,
        "public_client",
        lambda *args: httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=Stream()))),
    )

    async def check():
        response = await open_image("https://img.example.com/large.jpg")
        assert state["chunks"] == 1  # no full-body prefetch or disk cache

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                raise OSError("viewer left")

        try:
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        except Exception:
            pass
        assert state["closed"]
        assert state["chunks"] <= 2

    asyncio.run(check())


def test_concurrent_reversed_search_batches_reuse_ids(client):
    from concurrent.futures import ThreadPoolExecutor

    from ailab_platform.api.web_image_refs import register
    from ailab_platform.contracts.web_images import WebImageCandidate
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import Run

    runs = [client.post("/v1/runs", json={"prompt": "图片"}).json()["id"] for _ in range(2)]
    urls = [f"https://img.example.com/{uuid.uuid4().hex}/{i}.jpg" for i in range(3)]

    def search(index):
        with SessionLocal() as db:
            items = [WebImageCandidate(image=url) for url in (urls if index == 0 else urls[::-1])]
            result = register(db, db.get(Run, runs[index]), items)
            db.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(search, range(2)))
    assert [i["image"] for i in first] == [i["image"] for i in second][::-1]
