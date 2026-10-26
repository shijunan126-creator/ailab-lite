"""The crawler's only way out: an HTTP proxy that reaches public addresses only.

Chromium in the crawler resolves names, follows redirects and loads subresources by itself, so the
Agent's URL check (web._validate_url) cannot cover what a rendered page does. Every connection the
browser makes is sent here instead, and this process applies the same rule as the Agent's own
fetches: ports 80/443 only, every DNS answer public (web.public_addresses), connect to the address
that was checked. The crawler has no other route (deploy/base/crawler NetworkPolicy).

Supported: CONNECT host:443 (TLS stays end to end; the proxy never sees content) and absolute-form
plain-HTTP requests to port 80. Each tunnel has an idle timeout and a total deadline.

Run: ``python -m ailab_platform.egress`` (no platform credentials; the platform image is reused only
for this module and web.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from urllib.parse import urlsplit

from .web import WebError, public_addresses

log = logging.getLogger("ailab.egress")

PORT = int(os.environ.get("AILAB_EGRESS_PORT", "4750"))
MAX_CONNECTIONS = int(os.environ.get("AILAB_EGRESS_MAX_CONNECTIONS", "64"))
CONNECT_TIMEOUT = 10
IDLE_TIMEOUT = 30
TUNNEL_DEADLINE = 120
HEAD_LIMIT = 16 * 1024
BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}

slots = asyncio.Semaphore(MAX_CONNECTIONS)
_connect = asyncio.open_connection


class Refused(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status


def _target(host: str, port: int, allowed_port: int) -> tuple[str, int]:
    host = host.strip("[]").rstrip(".").lower()
    if not host or host in BLOCKED_HOSTS or host.endswith(".local"):
        raise Refused(403, "private hostname")
    if port != allowed_port:
        raise Refused(403, "port not allowed")
    return host, port


async def _open(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    try:
        # One connect budget covers DNS and all candidate addresses, rather than
        # allowing an unresponsive resolver to occupy every proxy slot indefinitely.
        async with asyncio.timeout(CONNECT_TIMEOUT):
            addresses = await public_addresses(host, port)
            last: Exception | None = None
            for address in addresses:
                try:
                    return await _connect(address, port)
                except OSError as exc:
                    last = exc
    except WebError as exc:
        raise Refused(403, str(exc)) from exc
    except TimeoutError as exc:
        raise Refused(502, "connection timed out") from exc
    raise Refused(502, f"cannot connect: {type(last).__name__ if last else 'no address'}")


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while chunk := await asyncio.wait_for(src.read(64 * 1024), IDLE_TIMEOUT):
            dst.write(chunk)
            await dst.drain()
    finally:
        with contextlib.suppress(Exception):
            dst.close()


async def _tunnel(a: tuple[asyncio.StreamReader, asyncio.StreamWriter], b: tuple[asyncio.StreamReader, asyncio.StreamWriter]) -> None:
    tasks = [asyncio.ensure_future(_pipe(a[0], b[1])), asyncio.ensure_future(_pipe(b[0], a[1]))]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), TUNNEL_DEADLINE)
    except TimeoutError:
        pass
    finally:
        for task in tasks:
            task.cancel()


def _reply(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
    text = {200: "Connection Established", 400: "Bad Request", 403: "Forbidden", 502: "Bad Gateway", 503: "Service Unavailable"}
    body = reason.encode()[:300]
    writer.write(
        f"HTTP/1.1 {status} {text.get(status, 'Error')}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
    )


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    upstream: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
    try:
        if slots.locked():
            raise Refused(503, "proxy is busy")
        async with slots:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), IDLE_TIMEOUT)
            except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, TimeoutError) as exc:
                raise Refused(400, "bad request head") from exc
            if len(head) > HEAD_LIMIT:
                raise Refused(400, "request head too large")
            lines = head.decode("latin-1").split("\r\n")
            try:
                method, target, version = lines[0].split(" ", 2)
            except ValueError as exc:
                raise Refused(400, "bad request line") from exc
            if method == "CONNECT":
                host, _, port = target.rpartition(":")
                if not port.isdigit():
                    raise Refused(400, "bad CONNECT target")
                upstream = await _open(*_target(host, int(port), 443))
                _reply(writer, 200, "")
                await writer.drain()
            else:
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname or parts.username or parts.password:
                    raise Refused(400, "only absolute http:// requests or CONNECT")
                upstream = await _open(*_target(parts.hostname, parts.port or 80, 80))
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                kept = [line for line in lines[1:] if line and not line.lower().startswith(("proxy-", "connection:", "keep-alive:"))]
                upstream[1].write(("\r\n".join([f"{method} {path} {version}", *kept, "Connection: close"]) + "\r\n\r\n").encode("latin-1"))
                await upstream[1].drain()
            await _tunnel((reader, writer), upstream)
    except Refused as exc:
        log.info("egress refused: %s", exc.status)  # never log the destination: it can carry user data
        with contextlib.suppress(Exception):
            _reply(writer, exc.status, str(exc))
            await writer.drain()
    except Exception as exc:
        log.warning("egress error: %s", type(exc).__name__)
    finally:
        for w in (writer, upstream[1] if upstream else None):
            if w is not None:
                with contextlib.suppress(Exception):
                    w.close()


async def serve(port: int = PORT) -> None:
    server = await asyncio.start_server(handle, "0.0.0.0", port, limit=HEAD_LIMIT)
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
