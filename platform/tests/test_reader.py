"""The side-pane reader: authenticated, public-web-only, bounded, error-mapped, and chrome-free."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from ailab_platform import web
from ailab_platform.api.main import app
from ailab_platform.reader import extract_blocks, markdown_to_blocks

AUTH = {"Authorization": "Bearer test-token"}

# The shape that made the reader useless before the extractor: real site chrome around a short
# article — language selectors, menu links, hot links, a share dialog, a footer of hotlines.
CHROME_PAGE = """
<html><head><title>以开创的超节点互联技术，引领AI基础设施新范式 - 华为</title></head><body>
<div class="lang-selector"><a href="/cn">China - 简体中文</a> <a href="/en">Global - English</a></div>
<nav role="navigation"><a href="/">返回主菜单</a><a href="/menu">选择区域/语言</a></nav>
<header class="header"><div class="hot-links"><a href="/mate90">HUAWEI Mate 90</a><a href="/cloud">华为云</a></div></header>
<article class="detail">
  <h1>以开创的超节点互联技术，引领AI基础设施新范式</h1>
  <p>[中国，上海，2025年9月18日] 女士们、先生们，各位老朋友、新朋友，大家上午好！欢迎来参加2025年华为全联接大会。</p>
  <p>第一、智能化的可持续，首先是算力的可持续；第二、中国半导体制造工艺将在相当长时间处于落后状态。</p>
  <p>今天，我想利用此机会，把我去年HC没有完成的任务完成：如何开创计算架构，打造超节点加集群算力解决方案。</p>
  <h2>昇腾芯片路标</h2>
  <ul><li>Ascend 950系列包括950PR和950DT两颗芯片</li><li>Ascend 960将在2027年四季度推出</li></ul>
  <ol><li>FP8算力达到8E FLOPS</li><li>互联带宽达到16PB/s</li></ol>
  <img src="/img/keynote.png" alt="发布会现场"/>
  <pre><code>ub = UnifiedBus</code></pre>
  <table><tr><td>产品</td><td>上市时间</td></tr><tr><td>Atlas 950超节点</td><td>2026年四季度</td></tr></table>
</article>
<div class="weixin-share"><p>微信扫一扫: 分享</p><p>二维码便可将本文分享至朋友圈。</p></div>
<footer><p>热线：950800（7*24小时）</p><p>相关新闻</p></footer>
</body></html>
"""


@pytest.fixture()
def api():
    with TestClient(app) as client:
        client.headers.update(AUTH)
        yield client


@pytest.fixture
def fake_page(monkeypatch):
    async def read(url):
        return {"url": url, "title": "页面标题", "blocks": [{"type": "p", "text": "正文内容", "lt": 0}], "truncated": False}

    monkeypatch.setattr("ailab_platform.web.read_page", read)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")


def test_reader_needs_a_session(api, fake_page):
    assert api.get("/v1/reader", params={"url": "https://example.com/a"}, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_reader_returns_the_extracted_page(api, fake_page):
    r = api.get("/v1/reader", params={"url": "https://example.com/a"})
    assert r.status_code == 200
    assert r.json() == {
        "url": "https://example.com/a",
        "title": "页面标题",
        "blocks": [{"type": "p", "text": "正文内容", "lt": 0}],
        "truncated": False,
    }


def test_reader_refuses_non_http_schemes(api, fake_page):
    for url in ("ftp://example.com/a", "file:///etc/passwd", "javascript:alert(1)"):
        assert api.get("/v1/reader", params={"url": url}).status_code == 400


def test_reader_maps_fetch_failures_to_502(api, monkeypatch):
    async def read(url):
        raise web.WebError("HTTP 404 from https://example.com/gone")

    monkeypatch.setattr("ailab_platform.web.read_page", read)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    r = api.get("/v1/reader", params={"url": "https://example.com/gone"})
    assert r.status_code == 502
    assert "404" in r.json()["detail"]


def test_reader_maps_a_stuck_page_to_504(api, monkeypatch):
    async def read(url):
        await asyncio.sleep(120)

    monkeypatch.setattr("ailab_platform.web.read_page", read)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    monkeypatch.setattr("ailab_platform.api.routes_reader.READ_TIMEOUT_SECONDS", 0.05)
    assert api.get("/v1/reader", params={"url": "https://example.com/slow"}).status_code == 504


def test_reader_is_off_when_web_access_is_off(api, fake_page, monkeypatch):
    monkeypatch.setattr(web.settings, "web_search_provider", "none")
    assert api.get("/v1/reader", params={"url": "https://example.com/a"}).status_code == 404


@pytest.fixture
def fake_html(monkeypatch):
    async def fetch(url):
        return {
            "url": url,
            "title": "T",
            "text": "<html><head><title>T</title></head><body><p>hello</p></body></html>",
            "content_type": "text/html",
            "truncated": False,
        }

    monkeypatch.setattr("ailab_platform.web.fetch_html", fetch)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")


def test_reader_render_serves_the_original_page(api, fake_html):
    r = api.get("/v1/reader/render", params={"url": "https://example.com/a"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert '<base href="https://example.com/a" target="_blank">' in r.text
    assert "hello" in r.text


def test_reader_render_refuses_non_html_pages(api, monkeypatch):
    async def fetch(url):
        return {"url": url, "title": "", "text": "just text", "content_type": "text/plain", "truncated": False}

    monkeypatch.setattr("ailab_platform.web.fetch_html", fetch)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    assert api.get("/v1/reader/render", params={"url": "https://example.com/a"}).status_code == 415


def test_reader_render_maps_fetch_failures_to_502(api, monkeypatch):
    async def fetch(url):
        raise web.WebError("HTTP 404 from https://example.com/gone")

    monkeypatch.setattr("ailab_platform.web.fetch_html", fetch)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    r = api.get("/v1/reader/render", params={"url": "https://example.com/gone"})
    assert r.status_code == 502
    assert "404" in r.json()["detail"]


def test_reader_render_needs_a_session(api, fake_html):
    assert (
        api.get("/v1/reader/render", params={"url": "https://example.com/a"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    )


def test_extract_blocks_drops_chrome_and_keeps_the_article():
    blocks = extract_blocks(CHROME_PAGE, "https://www.huawei.com/cn/news/2025/9/hc-xu-keynote-speech")
    joined = "\n".join(b.get("text", "") for b in blocks)
    assert "大家上午好" in joined
    assert "Ascend 950系列包括950PR和950DT两颗芯片" in joined
    assert "微信扫一扫" not in joined
    assert "热线：950800" not in joined
    assert "Global - English" not in joined
    assert "返回主菜单" not in joined
    assert "HUAWEI Mate 90" not in joined
    kinds = [b["type"] for b in blocks]
    assert "h1" in kinds and "h2" in kinds
    assert kinds.count("li") == 4
    assert any(b["type"] == "img" and b["src"].endswith("/img/keynote.png") and b["alt"] == "发布会现场" for b in blocks)
    assert "ub = UnifiedBus" in joined  # pre survives
    assert "产品 ｜ 上市时间" in joined and "Atlas 950超节点 ｜ 2026年四季度" in joined  # table rows as lines


def test_extract_blocks_lists_carry_ordered():
    blocks = extract_blocks(CHROME_PAGE, "https://www.huawei.com/cn/news/2025/9/hc-xu-keynote-speech")
    ordered = [b for b in blocks if b["type"] == "li"]
    assert [bool(b.get("ordered")) for b in ordered] == [False, False, True, True]


def test_extract_blocks_survives_garbage():
    assert extract_blocks("") == []
    assert extract_blocks("plain text, no tags at all") == [{"type": "p", "text": "plain text, no tags at all", "lt": 0}]
    # unclosed tags still yield their text; a parse explosion yields nothing
    assert [b["text"] for b in extract_blocks("<p><div><span>未闭合")] == ["未闭合"]


def test_markdown_to_blocks():
    blocks = markdown_to_blocks("# 标题\n\n正文第一行。\n- 项目一\n- 项目二\n\n1. 有序一\n```py\ncode()\n```")
    assert blocks[0] == {"type": "h1", "text": "标题", "lt": 0}
    assert {"type": "p", "text": "正文第一行。", "lt": 0} in blocks
    assert {"type": "li", "text": "项目一", "lt": 0} in blocks
    assert {"type": "li", "text": "有序一", "lt": 0, "ordered": True} in blocks
    assert blocks[-1] == {"type": "pre", "text": "code()", "lt": 0}


def test_article_header_hidden_and_unmatched_end_tags():
    blocks = extract_blocks(
        '<header><p>站点页眉</p></header><article><header class="article-header"><h1>文章标题</h1></header>'
        "<div hidden><p>隐藏内容</p></div><p>开头</bogus>结尾</p></article>"
    )
    text = "\n".join(b.get("text", "") for b in blocks)
    assert "文章标题" in text
    assert "站点页眉" not in text
    assert "隐藏内容" not in text
    assert "开头结尾" in text


def test_extractor_caps_a_single_large_block():
    from ailab_platform.reader import MAX_CHARS, extract_article

    blocks, truncated = extract_article("<article><p>" + "大" * (MAX_CHARS + 500) + "</p></article>")
    assert truncated
    assert sum(len(b.get("text", "")) for b in blocks) <= MAX_CHARS


def test_render_snapshot_blocks_navigation_and_escapes_base(api, monkeypatch):
    async def fetch(url):
        return {
            "url": 'https://example.com/?q="x"&a=1',
            "content_type": "text/html",
            "text": '<html><head><BASE href="https://wrong.test"><meta HTTP-EQUIV="Refresh" content="0;url=/jump">'
            '</head><body><p>文章</p><script>location="/jump"</script></body></html>',
        }

    monkeypatch.setattr(web, "fetch_html", fetch)
    monkeypatch.setattr(web.settings, "web_search_provider", "mock")
    response = api.get("/v1/reader/render", params={"url": "https://example.com/"})
    assert response.status_code == 200
    assert "wrong.test" not in response.text and "HTTP-EQUIV" not in response.text
    assert 'href="https://example.com/?q=&quot;x&quot;&amp;a=1"' in response.text
    assert "script-src 'none'" in response.headers["content-security-policy"]
    assert "文章" in response.text


def test_render_preserves_doctype_and_its_standards_mode():
    from ailab_platform.api.routes_reader import _FrameMarkup

    for html in ("<!doctype html><html><head><title>T</title></head><body>正文</body></html>", "<!doctype html><p>正文</p>"):
        rendered = _FrameMarkup(html).render("https://example.com/")
        assert rendered.startswith("<!doctype html>")
        assert rendered.count("<base ") == 1


def test_render_handles_carriage_returns_and_valueless_meta_attributes():
    from ailab_platform.api.routes_reader import _FrameMarkup

    html = '<!doctype html>\r<html><head><meta http-equiv><base href="https://wrong.test"></head><body>正文</body></html>'
    rendered = _FrameMarkup(html).render("https://example.com/")
    assert rendered.startswith("<!doctype html>")
    assert "wrong.test" not in rendered
    assert rendered.count("<base ") == 1
    assert "正文" in rendered


def test_document_chrome_classes_do_not_discard_rfi_style_article():
    markup = """<html class="site-header-enabled"><head><title>标题</title></head>
    <body class="u-header-sticky u-header-with-tag-list u-playlist-enabled">
      <header class="site-header"><p>导航广告</p></header>
      <main role="main"><div id="main-content" class="t-content t-content--article">
        <article class="t-content__article-wrapper" data-article-content>
          <div class="t-content__body u-clearfix"><p>文章开头及核心正文内容。</p><p>文章结尾。</p></div>
        </article>
      </div></main><footer><p>站点页脚</p></footer>
    </body></html>"""
    blocks = extract_blocks(markup, "https://www.rfi.fr/cn/example")
    text = "\n".join(b.get("text", "") for b in blocks)
    assert "文章开头及核心正文内容。" in text and "文章结尾。" in text
    assert "导航广告" not in text and "站点页脚" not in text
