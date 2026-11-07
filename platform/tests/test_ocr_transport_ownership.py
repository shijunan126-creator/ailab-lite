"""Lost executor cancellation receipts cannot be mistaken for stopped inference."""

import asyncio
import time

import httpx
import pytest

from ailab_platform import ocr_backend
from ailab_platform.config import settings


@pytest.mark.parametrize("acknowledged", [True, False])
def test_transport_failure_requires_explicit_stopped_receipt(monkeypatch, acknowledged):
    client = httpx.AsyncClient
    calls = []

    def handle(request):
        calls.append(request.method)
        if request.method == "POST":
            raise httpx.ReadError("lost response", request=request)
        return httpx.Response(200 if acknowledged else 503, json={"status": "stopped" if acknowledged else "pending"})

    monkeypatch.setattr(ocr_backend.httpx, "AsyncClient", lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    monkeypatch.setattr(settings, "ocr_executor_url", "http://isolated-ocr")
    expected = httpx.ReadError if acknowledged else ocr_backend.OCRExecutionUncertain
    with pytest.raises(expected):
        asyncio.run(ocr_backend.recognize(b"image", operation_id="owned-operation", deadline=time.time() + 10))
    assert calls == ["POST", "DELETE"]
