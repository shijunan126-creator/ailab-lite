"""Strict remote RapidOCR transport; never falls back to a language model or CPU."""

import asyncio
import base64

import httpx

from .config import settings


async def recognize(data: bytes) -> dict:
    if not settings.ocr_executor_url:
        raise ValueError("ocr_executor_unavailable")
    if len(data) > settings.ocr_page_max_bytes:
        raise ValueError("image_size_limit")
    async with asyncio.timeout(settings.ocr_executor_timeout_seconds):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ocr_executor_timeout_seconds, connect=5), follow_redirects=False
        ) as client:
            response = await client.post(
                settings.ocr_executor_url.rstrip("/") + "/recognize",
                headers={"Authorization": "Bearer " + settings.ocr_executor_token.get_secret_value()},
                json={"image": base64.b64encode(data).decode(), "required_backend": settings.ocr_required_backend},
            )
            response.raise_for_status()
            result = response.json()
    if result.get("backend") != settings.ocr_required_backend or not result.get("engine", "").startswith("rapidocr:"):
        raise ValueError("ocr_backend_mismatch")
    if settings.ocr_engine_fingerprint and result.get("fingerprint") != settings.ocr_engine_fingerprint:
        raise ValueError("ocr_engine_mismatch")
    if not isinstance(result.get("text"), str) or len(result["text"]) > 200000:
        raise ValueError("invalid_ocr_response")
    return result
