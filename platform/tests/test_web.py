from __future__ import annotations

import asyncio

import pytest

from ailab_platform import web


def test_extract_markdown_drops_scripts_and_keeps_title() -> None:
    body = "第一条内容，" * 60
    title, text, links = web.extract_markdown(
        "<html><head><title>OpenAI 新闻</title><style>x{}</style></head>"
        f"<body><script>alert(1)</script><article><h1>今日新闻</h1><p>{body}</p><a href='/b'>b</a></article>"
        "<nav>菜单</nav></body></html>",
        "https://example.com/a",
    )
    assert title in ("今日新闻", "OpenAI 新闻")  # Trafilatura may prefer the article heading over <title>
    assert "今日新闻" in text and "第一条内容" in text
    assert "alert" not in text
    assert links == ["https://example.com/b"]


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
    assert names == {
        "run_js",
        "web_search",
        "read_url",
        "save_skill",
        "load_skill",
        "ui_components",
        "memory",
        "recall_history",
        "search_library",
        "read_library_file",
        "extract_text",
        "resolve_attachment",
        "view_attachment",
    }
    work_names = {tool["function"]["name"] for tool in tools.tools_for_turn()}
    assert {"web_search", "read_url"} <= work_names


@pytest.mark.parametrize(
    "payload,error",
    [
        ({"results": [], "unresponsive_engines": [["google", "timeout"]]}, "engines failed"),
        ({"error": "upstream failed"}, "invalid response"),
        ({"results": [], "error": "upstream failed"}, "engines failed"),
        ([], "invalid response"),
        ({"results": [None]}, "invalid result"),
        ({"results": []}, None),
        ({"results": [{"url": "https://example.com"}], "unresponsive_engines": [["bing", "timeout"]]}, None),
    ],
)
def test_search_provider_errors_are_not_successful_empty_results(monkeypatch, payload, error):
    import httpx

    monkeypatch.setattr(web.settings, "web_search_provider", "searxng")
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    if error:
        with pytest.raises(web.WebError, match=error):
            asyncio.run(web.search("news today"))
    else:
        assert len(asyncio.run(web.search("news today"))) == len(payload["results"])
