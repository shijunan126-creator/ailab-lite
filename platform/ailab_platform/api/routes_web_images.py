"""Same-origin proxy for web pictures shown in answers: GET /v1/web-images/{token}.

The Portal's CSP only loads same-origin images, and hotlinking breaks on referer-checking sites and taints canvas
exports. The token (web_images.py) is the capability: it names one URL a web tool returned to the Agent, so no login
is needed (shared conversations render the same pictures) and the model cannot point a browser anywhere new.

The first view fetches the picture with the public-only client (web.py: every address and redirect checked, no
cookies), keeps it only if its bytes are a raster image, and stores it once under web-images/ in the derived-asset
ledger: bounded by AILAB_DERIVED_BUDGET_BYTES, expired after AILAB_DERIVED_CACHE_DAYS and evicted oldest-first under
pressure like other rebuildable caches. Later views and exports stream the stored copy without touching the site.
"""

import asyncio
import hashlib
import io
import logging
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from .. import metrics, web, web_images
from ..config import settings
from ..db import SessionLocal
from ..storage import get_storage
from . import derived_assets as assets

router = APIRouter(prefix="/v1", tags=["web-images"])
log = logging.getLogger("ailab.api.web_images")

MAX_BYTES = 8 * 1024 * 1024
FETCH_SECONDS = 10
# At most this many pictures are in API memory at once (each <= MAX_BYTES); further misses wait their turn.
_fetch_slots = asyncio.Semaphore(4)
_inflight: dict[str, asyncio.Task] = {}
SERVE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
    "Content-Disposition": "inline",
}


class Refused(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status, self.detail = status, detail


async def fetch(url: str) -> tuple[bytes, str]:
    """The picture's bytes and sniffed type. Raises Refused (413/415) or web.WebError (upstream failure)."""
    parsed = urlsplit(url)
    headers = {
        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,image/*;q=0.8",
        # Many CDNs refuse picture requests without a Referer; the picture's own site is the honest one to send.
        "Referer": f"{parsed.scheme}://{parsed.netloc}/",
    }
    async with asyncio.timeout(FETCH_SECONDS), web.public_client(FETCH_SECONDS, headers) as client, web.open_public(client, url) as resp:
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BYTES:
            raise Refused(413, "image is too large")
        body, truncated = await web._read_bounded(resp, MAX_BYTES + 1, deadline=time.monotonic() + FETCH_SECONDS)
    if truncated:
        raise Refused(413, "image is too large")
    content_type = web_images.sniff(body[:64])
    if not content_type:
        raise Refused(415, "not a supported image")
    return body, content_type


def _mock_picture(url: str) -> tuple[bytes, str]:
    from PIL import Image, ImageDraw

    seed = int(hashlib.sha256(url.encode()).hexdigest()[:6], 16)
    image = Image.new("RGB", (480, 320), (seed >> 16 & 0xFF, seed >> 8 & 0xFF, seed & 0xFF))
    ImageDraw.Draw(image).rectangle((40, 40, 440, 280), outline=(255, 255, 255), width=6)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue(), "image/png"


def _store(key: str, body: bytes, content_type: str) -> bool:
    """Charge the ledger, then write. Capacity or a concurrent writer elsewhere only means this copy is not kept."""
    digest = hashlib.sha256(body).hexdigest()
    with SessionLocal() as db:
        try:
            grant = assets.reserve(db, key, len(body), "web", kind="cache")
            db.commit()
        except HTTPException as exc:
            db.rollback()
            log.info("web image not cached: %s", exc.detail)
            return False
        done = False
        try:
            get_storage().put_bytes(key, body, content_type)
            assets.finish(db, key, grant["token"], len(body), digest)
            db.commit()
            done = True
        finally:
            if not done:
                db.rollback()
                assets.abandon(db, key, grant["token"])  # the bytes stay charged until GC deletes them
                db.commit()
    return True


def _open_stored(key: str):
    """(content type, chunks) of the stored copy, or None when there is none to serve."""
    storage = get_storage()
    if not storage.exists(key):
        return None
    chunks = storage.stream(key)
    try:
        first = next(chunks, b"")
    except Exception as exc:
        chunks.close()
        code = ((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code")
        if isinstance(exc, FileNotFoundError) or code in {"404", "NoSuchKey", "NotFound"}:
            return None  # evicted between the check and the read
        raise
    content_type = web_images.sniff(first[:64])
    if not content_type:
        chunks.close()
        return None

    def body():
        try:
            yield first
            yield from chunks
        finally:
            chunks.close()

    return content_type, body()


async def _fetch_and_store(url: str, key: str) -> tuple[bytes, str]:
    async with _fetch_slots:
        body, content_type = await fetch(url)
    try:
        await run_in_threadpool(_store, key, body, content_type)
    except Exception:
        log.warning("web image cache write failed", exc_info=True)
    return body, content_type


async def _coalesced(url: str, key: str) -> tuple[bytes, str]:
    """One upstream fetch per picture at a time in this process; a viewer that leaves does not cancel it for others."""
    task = _inflight.get(key)
    if task is None or task.get_loop() is not asyncio.get_running_loop():
        task = asyncio.create_task(_fetch_and_store(url, key))
        _inflight[key] = task
        task.add_done_callback(lambda done: _inflight.pop(key, None) if _inflight.get(key) is done else None)
    return await asyncio.shield(task)


def _failure(status: int, detail: str) -> Response:
    return Response(content=detail, status_code=status, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


@router.get("/web-images/{token}")
async def web_image(token: str, request: Request) -> Response:
    url = web_images.verify(token)
    if url is None:
        raise HTTPException(404, "not found")
    key = web_images.cache_key(url)
    headers = {**SERVE_HEADERS, "ETag": f'"{key.rsplit("/", 1)[1][:32]}"'}  # the URL is the identity; its copy never changes
    if headers["ETag"] in {tag.strip() for tag in request.headers.get("if-none-match", "").split(",")}:
        return Response(status_code=304, headers=headers)
    if settings.web_search_provider == "mock" and url.startswith(web.MOCK_IMAGE_BASE):
        body, content_type = _mock_picture(url)
        return Response(content=body, media_type=content_type, headers=headers)
    stored = await run_in_threadpool(_open_stored, key)
    if stored is not None:
        metrics.observe_web_image("hit")
        content_type, chunks = stored
        return StreamingResponse(chunks, media_type=content_type, headers=headers)
    try:
        body, content_type = await _coalesced(url, key)
    except Refused as exc:
        metrics.observe_web_image("refused")
        return _failure(exc.status, exc.detail)
    except (web.WebError, TimeoutError) as exc:
        metrics.observe_web_image("failed")
        log.info("web image fetch failed: %s", type(exc).__name__)
        return _failure(502, "image could not be fetched")
    except Exception:
        metrics.observe_web_image("failed")
        log.warning("web image fetch failed unexpectedly", exc_info=True)
        return _failure(502, "image could not be fetched")
    metrics.observe_web_image("fetched")
    return Response(content=body, media_type=content_type, headers=headers)
