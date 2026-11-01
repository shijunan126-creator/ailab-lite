"""Generated UI in answers (HLD §6, P4), driven by the offline model's GenUI markers."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .conftest import select_model, wait_models

pytestmark = pytest.mark.e2e

COPY_MARKDOWN = "| 名称 | 数量 |\n|---|--:|\n| 苹果 | 3 |"
COPY_CODE = 'name = "AILab"\nprint(name)'


def _ask(page, prompt: str) -> None:
    wait_models(page)
    select_model(page, "test-fast")
    page.get_by_role("textbox", name="消息", exact=True).fill(prompt)
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/cnv_"), timeout=15000)
    page.wait_for_function("(n) => document.querySelectorAll('.turn-meta').length >= n", arg=1, timeout=90000)


@pytest.fixture()
def copy_answer(logged_in, context, stack):
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=stack.portal)
    _ask(logged_in, "[[genui-copy]]")
    answer = logged_in.locator(".msg.assistant").last
    expect(answer.locator(".genui-block[data-state='ok']")).to_have_count(2, timeout=20000)
    expect(answer.locator(".md-codeblock code")).to_have_text(COPY_CODE)
    return answer


def _clipboard_text(page) -> dict[str, str]:
    return page.evaluate("""async () => {
        const result = {};
        for (const item of await navigator.clipboard.read()) {
            for (const type of item.types) {
                if (type.startsWith('text/')) result[type] = await (await item.getType(type)).text();
            }
        }
        return result;
    }""")


def test_chart_hover_downloads_and_copies_a_png(copy_answer, page) -> None:
    chart = copy_answer.locator(".genui-block").filter(has_text="请求概览")
    tools = chart.locator(".genui-tools")
    page.mouse.move(0, 0)
    expect(tools).to_have_css("opacity", "0")
    chart.locator(".gu-heading").hover()
    expect(tools).to_have_css("opacity", "1")
    expect(tools).to_be_visible()
    with page.expect_download() as download_info:
        tools.get_by_role("button", name="下载图片", exact=True).click()
    download = download_info.value
    assert download.suggested_filename == "请求概览.png"
    picture = download.path().read_bytes()
    assert picture.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(picture) > 1024
    copy = tools.get_by_role("button", name="复制为图片", exact=True)
    copy.click()
    expect(tools.locator("button").first).to_have_attribute("data-phase", "done", timeout=20000)
    expect(tools.locator("button").first).to_have_attribute("aria-label", "已复制")
    assert page.evaluate("async () => (await navigator.clipboard.read()).some(item => item.types.includes('image/png'))")


def test_table_only_block_copies_html_and_numeric_markdown(copy_answer, page) -> None:
    block = copy_answer.locator(".genui-block").filter(has_text="地区明细")
    expect(block.locator(".genui-tools")).to_have_count(0)
    table = block.locator(".table-frame")
    page.mouse.move(0, 0)
    expect(table.locator(".table-tools")).to_have_css("opacity", "0")
    table.locator("table").hover()
    expect(table.locator(".table-tools")).to_have_css("opacity", "1")
    table.get_by_role("button", name="复制表格", exact=True).click()
    expect(table.get_by_role("button", name="已复制", exact=True)).to_be_visible()
    copied = _clipboard_text(page)
    assert "<table" in copied["text/html"]
    assert "---:" in copied["text/plain"]
    assert "| 地区 | 营收 |" in copied["text/plain"]
    assert "| 华东 | 1,280 |" in copied["text/plain"]


def test_markdown_table_copies_its_exact_source(copy_answer, page) -> None:
    table = copy_answer.locator(".table-frame").filter(has_text="苹果")
    table.locator("table").hover()
    expect(table.locator(".table-tools")).to_have_css("opacity", "1")
    table.get_by_role("button", name="复制表格", exact=True).click()
    expect(table.get_by_role("button", name="已复制", exact=True)).to_be_visible()
    copied = _clipboard_text(page)
    assert copied["text/plain"] == COPY_MARKDOWN
    assert "<table" in copied["text/html"]


def test_code_block_copies_only_the_exact_code(copy_answer, page) -> None:
    code = copy_answer.locator(".md-codeblock")
    code.hover()
    code.get_by_role("button", name="复制代码", exact=True).click()
    expect(code.get_by_role("button", name="已复制", exact=True)).to_be_visible()
    assert _clipboard_text(page)["text/plain"] == COPY_CODE


def test_text_and_ui_blocks_render_in_order_and_a_button_sends_its_message(logged_in) -> None:
    page = logged_in
    _ask(page, "[[genui-demo]] 看看请求情况")
    answer = page.locator(".msg.assistant").last
    answer.locator(".genui-block[data-state='ok']").nth(2).wait_for(timeout=20000)
    assert answer.locator(".genui-block").count() == 3
    text = answer.inner_text()
    assert text.index("三个指标") < text.index("请求") < text.index("成功请求占多数") < text.index("以上为离线合成数据")
    assert "```" not in text and "root =" not in text  # source never shows when the block renders
    assert answer.locator(".genui-block svg").count() >= 1  # the bar chart
    answer.get_by_role("button", name="按天拆分", exact=True).click()
    page.locator(".msg.user", has_text="把请求按天拆分").wait_for(timeout=15000)
    page.wait_for_function("() => document.querySelectorAll('.turn-meta').length >= 2", timeout=90000)


def test_a_block_that_cannot_render_falls_back_without_losing_the_answer(logged_in) -> None:
    page = logged_in
    _ask(page, "[[genui-bad]]")
    answer = page.locator(".msg.assistant").last
    answer.locator(".genui-fallback", has_text="无法显示此内容").first.wait_for(timeout=20000)
    text = answer.inner_text()
    assert "非法组件演示" in text and "未闭合块" in text


def test_markup_from_the_model_stays_text(logged_in) -> None:
    page = logged_in
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    _ask(page, "[[genui-inject]]")
    answer = page.locator(".msg.assistant").last
    answer.locator(".genui-block[data-state='ok']").wait_for(timeout=20000)
    assert "<script>alert(1)</script>" in answer.inner_text()
    assert answer.locator("script, img, a[href^='javascript']").count() == 0
    page.wait_for_timeout(300)
    assert dialogs == []


def test_refresh_share_and_print_rebuild_the_ui_with_read_only_actions(logged_in, stack, context) -> None:
    page = logged_in
    _ask(page, "[[genui-demo]]")
    conv = page.url.rsplit("/", 1)[-1]
    page.reload()
    page.locator(".genui-block[data-state='ok']").nth(2).wait_for(timeout=20000)
    page.evaluate("""Object.defineProperty(navigator, 'clipboard', {
        configurable: true, value: { writeText: async text => { window.genuiCopied = text; } }
    })""")
    page.get_by_role("button", name="复制回答", exact=True).click()
    copied = page.evaluate("window.genuiCopied")
    assert "root =" not in copied and "320" in copied and "以上为离线合成数据" in copied
    response = page.request.post(
        stack.portal + "/api/v1/shares", data={"kind": "conversation", "conversation_id": conv, "audience": "everyone"}
    )
    assert response.ok, response.text()
    shared = response.json()
    try:
        page.goto(stack.portal + "/s/" + shared["id"])
        action = page.get_by_role("button", name="按天拆分", exact=True)
        action.wait_for()
        assert action.is_disabled()
        assert page.locator(".gu-chart svg").count() == 1
        context.add_init_script("window.print = () => { window.genuiPrinted = true; }")
        page.goto(stack.portal + "/print/" + conv)
        page.wait_for_function("() => window.genuiPrinted === true")
        assert page.locator(".gu-chart svg").count() == 1
        assert page.get_by_role("button", name="按天拆分", exact=True).is_disabled()
        assert "以上为离线合成数据" in page.locator(".print-a").inner_text()
    finally:
        page.request.delete(stack.portal + "/api/v1/shares/" + shared["id"])
