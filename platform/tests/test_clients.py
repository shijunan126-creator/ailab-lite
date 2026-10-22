"""Internal clients keep one transport per process instead of one per call."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ailab_platform.clients import AsyncPlatformClient, ClientError, PlatformClient


class _Response:
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {"ok": True}


class _SyncClient:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    def request(self, *args, **kwargs) -> _Response:
        self.calls += 1
        return _Response()

    def close(self) -> None:
        self.closed = True


class _AsyncClient:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = False

    async def request(self, *args, **kwargs) -> _Response:
        self.calls += 1
        return _Response()

    async def aclose(self) -> None:
        self.closed = True


def test_sync_client_reuses_one_transport(monkeypatch) -> None:
    fake = _SyncClient()
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake)

    client = PlatformClient(base_url="http://api", token="t")
    client.get_run("r1")
    client.get_run("r2")

    assert fake.calls == 2
    client.close()
    assert fake.closed is True


def test_async_client_reuses_one_transport(monkeypatch) -> None:
    fake = _AsyncClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: fake)
    client = AsyncPlatformClient(base_url="http://api", token="t")

    async def exercise() -> None:
        await client.get_run("r1")
        await client.get_run("r2")
        await client.aclose()

    asyncio.run(exercise())
    assert fake.calls == 2
    assert fake.closed is True


def test_client_rejects_error_responses(monkeypatch) -> None:
    class Bad(_Response):
        status_code = 503
        text = "upstream unavailable"

    class BadClient(_SyncClient):
        def request(self, *args, **kwargs) -> _Response:
            return Bad()

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: BadClient())
    with pytest.raises(ClientError, match="503"):
        PlatformClient(base_url="http://api", token="t").get_run("r")
