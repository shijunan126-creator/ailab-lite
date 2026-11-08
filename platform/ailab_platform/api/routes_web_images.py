"""Authorized, on-demand web pictures. Transfer bytes in bounded chunks; never write image storage."""

import asyncio
import hashlib
import io
import json
import time
from contextlib import AsyncExitStack
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import metrics, web, web_images
from ..config import settings
from ..deps import current_user, get_db
from ..models import User
from .web_image_refs import ID, authorized

router = APIRouter(prefix="/v1", tags=["web-images"])
MAX_BYTES = 8 * 1024 * 1024
FETCH_SECONDS = 10
CHUNK_BYTES = 64 * 1024
_fetch_slots = asyncio.Semaphore(4)
SERVE_HEADERS = {
    "Cache-Control": "private, max-age=300",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
    "Content-Disposition": "inline",
}


class Refused(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


def _mock_picture(url: str) -> tuple[bytes, str]:
    from PIL import Image, ImageDraw

    seed = int(hashlib.sha256(url.encode()).hexdigest()[:6], 16)
    image = Image.new("RGB", (480, 320), (seed >> 16 & 0xFF, seed >> 8 & 0xFF, seed & 0xFF))
    ImageDraw.Draw(image).rectangle((40, 40, 440, 280), outline=(255, 255, 255), width=6)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue(), "image/png"


def lookup(
    token: str, share_id: str | None = Query(default=None, max_length=40), user: User = Depends(current_user), db: Session = Depends(get_db)
):
    if ID.fullmatch(token):
        return authorized(db, token, user, share_id)
    # Preserve already-issued legacy references without creating any new signed paths or stored bytes.
    url = web_images.verify(token)
    if url is None:
        raise HTTPException(404, "image not found")
    return {"url": url, "thumbnail_url": "", "page_url": "", "title": ""}


@router.get("/web-images/{token}/meta")
def metadata(image: dict = Depends(lookup)):
    return Response(
        content=json.dumps({"source": image["page_url"], "title": image["title"]}),
        media_type="application/json",
        headers={"Cache-Control": "private, no-store"},
    )


class ImageStream(StreamingResponse):
    def __init__(self, body, stack, content_type):
        super().__init__(body, media_type=content_type, headers=SERVE_HEADERS)
        self.stack = stack

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Also covers disconnect before the iterator's first step or while send() is blocked.
            await self.stack.aclose()


async def open_image(url):
    stack = AsyncExitStack()
    try:
        deadline = time.monotonic() + FETCH_SECONDS
        async with asyncio.timeout(FETCH_SECONDS):
            await stack.enter_async_context(_fetch_slots)
            parsed = urlsplit(url)
            client = await stack.enter_async_context(
                web.public_client(
                    FETCH_SECONDS,
                    {
                        "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif",
                        "Accept-Encoding": "identity",
                        "Referer": f"{parsed.scheme}://{parsed.netloc}/",
                    },
                )
            )
            response = await stack.enter_async_context(web.open_public(client, url))
            declared = response.headers.get("content-length", "")
            expected = int(declared) if declared.isdigit() else None
            if expected is not None and expected > MAX_BYTES:
                raise Refused(413, "image is too large")
            if response.headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                raise Refused(415, "compressed image transfer is not supported")
            chunks = response.aiter_raw(chunk_size=CHUNK_BYTES)
            first = b""
            while len(first) < 64:
                chunk = await anext(chunks, b"")
                if not chunk:
                    break
                first += chunk
            if len(first) > MAX_BYTES:
                raise Refused(413, "image is too large")
            content_type = web_images.sniff(first[:64])
            if not content_type:
                raise Refused(415, "not a supported image")

        async def body():
            total = len(first)
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                yield first
                async for chunk in chunks:
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise Refused(413, "image is too large")
                    yield chunk
                if expected is not None and total != expected:
                    raise Refused(502, "incomplete image")

        return ImageStream(body(), stack, content_type)
    except BaseException:
        await stack.aclose()
        raise


@router.get("/web-images/{token}")
async def web_image(
    request: Request, variant: str = Query(default="thumb", pattern="^(thumb|original|fallback)$"), image: dict = Depends(lookup)
):
    original, thumb = image["url"], image["thumbnail_url"]
    urls = list(dict.fromkeys([original, thumb] if variant == "original" else [thumb, original]))
    if variant == "fallback":
        urls = [thumb] if thumb and thumb != original else []
    for url in filter(None, urls):
        if settings.web_search_provider == "mock" and url.startswith(web.MOCK_IMAGE_BASE):
            body, content_type = _mock_picture(url)
            return Response(content=body, media_type=content_type, headers=SERVE_HEADERS)
        try:
            result = await open_image(url)
            metrics.observe_web_image("fetched")
            return result
        except (Refused, web.WebError, TimeoutError):
            continue  # Only before headers. A broken original stream retries its thumbnail in the Portal.
    metrics.observe_web_image("failed")
    return Response(content="image could not be fetched", status_code=502, headers={"Cache-Control": "no-store"})
