"""The crawler's egress proxy applies web.py's public-address rule to every connection a rendered page makes."""

from __future__ import annotations

import asyncio

import pytest

from ailab_platform import egress
from ailab_platform.web import WebError


async def _ask(port: int, head: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(head)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(4096), 5)
    writer.close()
    return data


async def _with_proxy(fn):
    server = await asyncio.start_server(egress.handle, "127.0.0.1", 0, limit=egress.HEAD_LIMIT)
    port = server.sockets[0].getsockname()[1]
    try:
        return await fn(port)
    finally:
        server.close()
        await server.wait_closed()


@pytest.fixture
def dns(monkeypatch):
    async def public(host, port):
        if host in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "rebind.test"):
            raise WebError(f"refusing non-public address: {host}")
        return ["93.184.216.34"]

    monkeypatch.setattr(egress, "public_addresses", public)


@pytest.mark.parametrize(
    ("head", "status"),
    [
        (b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT 169.254.169.254:443 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT rebind.test:443 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT example.com:22 HTTP/1.1\r\n\r\n", b"403"),
        (b"CONNECT localhost:443 HTTP/1.1\r\n\r\n", b"403"),
        (b"GET http://10.0.0.1/ HTTP/1.1\r\nHost: 10.0.0.1\r\n\r\n", b"403"),
        (b"GET http://example.com:8080/ HTTP/1.1\r\n\r\n", b"403"),
        (b"GET /relative HTTP/1.1\r\nHost: example.com\r\n\r\n", b"400"),
        (b"GET http://user:pw@example.com/ HTTP/1.1\r\n\r\n", b"400"),
    ],
)
def test_non_public_or_unsupported_destinations_are_refused(dns, head, status):
    reply = asyncio.run(_with_proxy(lambda port: _ask(port, head)))
    assert reply.split(b" ")[1] == status


def test_plain_http_is_forwarded_to_the_checked_address_without_proxy_headers(dns, monkeypatch):
    seen: dict = {}

    async def run(port):
        async def origin(reader, writer):
            seen["head"] = await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
            writer.close()

        upstream = await asyncio.start_server(origin, "127.0.0.1", 0)
        origin_port = upstream.sockets[0].getsockname()[1]

        async def connect(address, port_):
            seen["address"] = (address, port_)
            return await asyncio.open_connection("127.0.0.1", origin_port)

        monkeypatch.setattr(egress, "_connect", connect)
        try:
            return await _ask(port, b"GET http://example.com/p?q=1 HTTP/1.1\r\nHost: example.com\r\nProxy-Authorization: x\r\n\r\n")
        finally:
            upstream.close()

    reply = asyncio.run(_with_proxy(run))
    assert reply.endswith(b"ok")
    assert seen["address"] == ("93.184.216.34", 80)
    assert seen["head"].startswith(b"GET /p?q=1 HTTP/1.1\r\n")
    assert b"Proxy-Authorization" not in seen["head"] and b"Connection: close" in seen["head"]


def test_proxy_connect_deadline_includes_dns(monkeypatch):
    async def slow_dns(host, port):
        await asyncio.sleep(0.15)
        return ["93.184.216.34"]

    monkeypatch.setattr(egress, "public_addresses", slow_dns)
    monkeypatch.setattr(egress, "CONNECT_TIMEOUT", 0.03)

    async def connected(*args):
        return None, None

    monkeypatch.setattr(egress, "_connect", connected)

    async def check():
        with pytest.raises(egress.Refused, match="timed out"):
            await egress._open("example.com", 443)

    asyncio.run(check())
