"""Generated UI in answers (HLD §6, P4), driven by the offline model's GenUI markers."""

from __future__ import annotations

import re

import pytest

from .conftest import select_model, wait_models

pytestmark = pytest.mark.e2e


def _ask(page, prompt: str) -> None:
    wait_models(page)
    select_model(page, "test-fast")
    page.get_by_role("textbox", name="消息", exact=True).fill(prompt)
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/cnv_"), timeout=15000)
    page.wait_for_function("(n) => document.querySelectorAll('.turn-meta').length >= n", arg=1, timeout=90000)


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
