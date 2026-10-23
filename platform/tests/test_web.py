from __future__ import annotations

import asyncio

import pytest

from ailab_platform import web


def test_extract_html_strips_scripts_and_keeps_title() -> None:
    title, text = web.extract_html(
        "<html><head><title>OpenAI 新闻</title><style>x{}</style></head>"
        "<body><script>alert(1)</script><h1>今日新闻</h1><p>第一条&nbsp;内容</p>"
        "<nav>菜单</nav></body></html>"
    )
    assert title == "OpenAI 新闻"
    assert "今日新闻" in text
    assert "第一条 内容" in text
    assert "alert" not in text


def test_private_and_non_web_urls_are_rejected() -> None:
    for url in [
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.8/",
        "http://localhost/",
        "file:///etc/passwd",
        "http://example.com:8080/",
        "http://user:pass@example.com/",
    ]:
        with pytest.raises(web.WebError):
            web._validate_url(url)


def test_public_url_is_normalized() -> None:
    assert web._validate_url("https://example.com/a?b=1#frag") == "https://example.com/a?b=1"


def test_read_url_rejects_private_host_before_connecting() -> None:
    with pytest.raises(web.WebError):
        asyncio.run(web.read_url("http://127.0.0.1/"))


def test_mock_search(monkeypatch) -> None:
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    results = asyncio.run(web.search("OpenAI 新闻", 3))
    assert len(results) == 3
    assert results[0]["title"].startswith("OpenAI 新闻")
    assert results[0]["url"].startswith("https://example.com/")


def test_chat_mode_offers_only_read_only_web_tools(monkeypatch) -> None:
    from ailab_platform.agent import tools

    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    names = {tool["function"]["name"] for tool in tools.tools_for_chat()}
    assert names == {"web_search", "read_url"}
    work_names = {tool["function"]["name"] for tool in tools.tools_for_turn()}
    assert {"web_search", "read_url"} <= work_names
