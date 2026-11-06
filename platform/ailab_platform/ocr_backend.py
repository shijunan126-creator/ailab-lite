"""Strict remote RapidOCR transport; never falls back to a language model or CPU."""

import asyncio
import base64
import time

import httpx

from .config import settings


class OCRExecutionUncertain(RuntimeError):
    """The executor has not acknowledged that its owned inference has stopped."""


async def recognize(data: bytes, *, operation_id: str | None = None, deadline: float | None = None) -> dict:
    if not settings.ocr_executor_url:
        raise ValueError("ocr_executor_unavailable")
    if len(data) > settings.ocr_page_max_bytes:
        raise ValueError("image_size_limit")
    timeout = min(settings.ocr_executor_timeout_seconds, (deadline - time.time()) if deadline else settings.ocr_executor_timeout_seconds)
    if timeout <= 0:
        raise TimeoutError("OCR deadline expired")
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(5, timeout)), follow_redirects=False) as client:
        headers = {"Authorization": "Bearer " + settings.ocr_executor_token.get_secret_value()}
        completed = False
        try:
            async with asyncio.timeout(timeout):
                if operation_id:
                    response = await client.post(
                        settings.ocr_executor_url.rstrip("/") + "/recognize-image",
                        content=data,
                        headers={
                            **headers,
                            "Content-Type": "application/octet-stream",
                            "X-Ocr-Operation": operation_id,
                            "X-Ocr-Deadline": str(deadline or time.time() + timeout),
                            "X-Ocr-Backend": settings.ocr_required_backend,
                        },
                    )
                else:
                    response = await client.post(
                        settings.ocr_executor_url.rstrip("/") + "/recognize",
                        headers=headers,
                        json={"image": base64.b64encode(data).decode(), "required_backend": settings.ocr_required_backend},
                    )
                response.raise_for_status()
                if len(response.content) > 1024 * 1024:
                    raise ValueError("invalid_ocr_response")
                result = response.json()
                completed = True
        finally:
            if operation_id and not completed:
                # The executor kills the owned inference child rather than
                # allowing an abandoned HTTP request to keep running kernels.
                try:
                    async with asyncio.timeout(3):
                        stopped = await asyncio.shield(
                            client.delete(settings.ocr_executor_url.rstrip("/") + "/operations/" + operation_id, headers=headers, timeout=3)
                        )
                        stopped.raise_for_status()
                        if stopped.json().get("status") != "stopped":
                            raise ValueError("OCR cancellation not acknowledged")
                except (Exception, asyncio.CancelledError) as exc:
                    raise OCRExecutionUncertain("OCR cancellation unconfirmed") from exc
    if result.get("backend") != settings.ocr_required_backend or not result.get("engine", "").startswith("rapidocr:"):
        raise ValueError("ocr_backend_mismatch")
    if settings.ocr_engine_fingerprint and result.get("fingerprint") != settings.ocr_engine_fingerprint:
        raise ValueError("ocr_engine_mismatch")
    if not isinstance(result.get("text"), str) or len(result["text"]) > 200000:
        raise ValueError("invalid_ocr_response")
    return result
