"""Crawler process gone (closed port): read_url fails fast with a defined tool error and does not fetch by itself."""

from __future__ import annotations

import asyncio
import socket

import pytest

from ailab_platform import web
from ailab_platform.agent import tools


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_read_url_with_the_crawler_down_is_a_tool_error(monkeypatch):
    async def public(host, port):
        return ["93.184.216.34"]

    async def no_static(url):
        raise AssertionError("must not fall back to the Agent's own fetch")

    monkeypatch.setattr(web.settings, "web_read_provider", "crawl4ai")
    monkeypatch.setattr(web.settings, "crawler_url", f"http://127.0.0.1:{_closed_port()}")
    monkeypatch.setattr(web.settings, "crawler_token", "t" * 32)
    monkeypatch.setattr(web, "public_addresses", public)
    monkeypatch.setattr(web, "fetch_html", no_static)
    with pytest.raises(tools.ToolError, match="page reader unavailable"):
        ctx = tools.ToolContext(messages=[{"role": "user", "content": "看看 https://example.com/a"}])
        asyncio.run(asyncio.wait_for(tools.execute_tool("read_url", {"url": "https://example.com/a"}, "", None, ctx), 10))
