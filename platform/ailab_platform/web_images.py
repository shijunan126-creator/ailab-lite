"""Raster signature sniffing and compatibility for signed image paths in older answers.

New web tools register address-only records through the API and return short IDs. No new model result uses
these signed paths; verification remains so existing answers do not break on upgrade.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac

from .config import settings

PATH = "/v1/web-images/"
MAX_URL = 1500  # the model copies the token verbatim; a longer URL costs output tokens and copy errors
MAC_CHARS = 22  # 132 bits
MAX_TOKEN = (MAX_URL * 4 + 2) // 3 + 1 + MAC_CHARS
# Published on purpose: only the dev/test profiles fall back to it, and secure refuses to start without a real key.
DEV_KEY = b"ailab-dev-web-image-key-not-secret"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _key() -> bytes | None:
    configured = settings.web_image_key.get_secret_value()
    if configured:
        return configured.encode()
    return None if settings.profile == "secure" else DEV_KEY


def _mac(key: bytes, url: str) -> str:
    return _b64(hmac.digest(key, b"web-image-v1\0" + url.encode(), "sha256"))[:MAC_CHARS]


def reference(url: str) -> str | None:
    """The path the model may put into UI for this already-checked public URL; None when it cannot be referenced."""
    key = _key()
    if key is None or len(url.encode()) > MAX_URL:
        return None
    return f"{PATH}{_b64(url.encode())}.{_mac(key, url)}"


def verify(token: str) -> str | None:
    """The URL a token was issued for, or None for anything forged, tampered or malformed."""
    key = _key()
    if key is None or len(token) > MAX_TOKEN or token.count(".") != 1:
        return None
    body, mac = token.split(".")
    if len(mac) != MAC_CHARS:
        return None
    try:
        url = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
    except (binascii.Error, ValueError):
        return None
    if not hmac.compare_digest(mac, _mac(key, url)):
        return None
    return url if url.startswith(("http://", "https://")) else None


def cache_key(url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()
    return f"web-images/{digest[:2]}/{digest}"


def sniff(head: bytes) -> str | None:
    """The image type from its first bytes. Never trusts a header: SVG and HTML are refused even when labelled image/*."""
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        box = int.from_bytes(head[:4], "big")
        brands = head[8 : min(max(box, 12), 64)]
        if any(brands[i : i + 4] in (b"avif", b"avis") for i in range(0, len(brands) - 3, 4)):
            return "image/avif"
    return None
