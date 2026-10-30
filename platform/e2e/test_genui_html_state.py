"""Generated pages (HtmlArtifact, HLD §6.16) run only in the sandboxed frame route; control state survives a reload
and reaches share and print (ui-state); the Portal and the frame route carry their CSP."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .conftest import select_model, wait_models

pytestmark = pytest.mark.e2e


def _ask(page, prompt: str) -> None:
    wait_models(page)
    select_model(page, "test-fast")
    page.get_by_role("textbox", name="消息", exact=True).fill(prompt)
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/cnv_"), timeout=15000)
    page.wait_for_function("() => document.querySelectorAll('.turn-meta').length >= 1", timeout=90000)


def test_a_generated_page_runs_without_network_storage_or_cookies(logged_in) -> None:
    page = logged_in
    _ask(page, "[[genui-html]] 抛硬币")
    page.locator(".gu-html[data-state='complete'] .gu-html-frame:not(.gu-html-pending)").wait_for(timeout=20000)
    frame = page.frame_locator(".gu-html-frame:not(.gu-html-pending)")
    expect(frame.locator("body")).to_have_attribute("data-ran", "1")
    expect(frame.locator("#net")).to_have_text("net:blocked")
    expect(frame.locator("#store")).to_have_text("store:blocked")
    frame.get_by_role("button", name="抛 10 次").click()
    expect(frame.locator("#count")).to_contain_text("已抛 10 次")
    # the page never sees the conversation: an opaque origin, nothing of the Portal's document
    assert page.locator(".gu-html-frame").first.get_attribute("sandbox") == "allow-scripts"
    expect(page.locator(".gu-html-note")).to_have_count(0)


def test_a_page_whose_block_never_closed_does_not_run(logged_in) -> None:
    page = logged_in
    _ask(page, "[[genui-html-cut]]")
    html = page.locator(".gu-html").last
    expect(html).to_have_attribute("data-state", "cut")
    expect(html.locator(".gu-html-note")).to_have_text("页面没有写完，未运行")
    expect(html.locator("iframe")).to_have_count(0)
    html.get_by_role("button", name="查看源码").click()  # what was written stays readable
    expect(html.locator(".gu-html-source")).to_contain_text('id="go">抛 10 次</button>')


def test_a_page_that_navigates_away_is_stopped(logged_in) -> None:
    page = logged_in
    _ask(page, "[[genui-html-escape]]")
    expect(page.locator(".gu-html-note")).to_contain_text("页面尝试打开其它地址，已停止运行", timeout=20000)
    expect(page.locator(".gu-html-frame")).to_have_count(0)


def test_control_state_survives_a_reload_and_reaches_share_and_print(logged_in, stack, context) -> None:
    page = logged_in
    _ask(page, "[[genui-interactive]] 算一下费用")
    block = page.locator(".genui-block[data-state='ok']")
    total = block.locator(".gu-metric-number")
    expect(total).to_have_text("980")
    with page.expect_response(lambda r: r.url.endswith("/ui-state") and r.request.method == "PUT") as saved:
        block.get_by_role("slider", name="人数").fill("60")
    assert saved.value.status == 204
    expect(total).to_have_text("2,940")  # 60 * 49
    conv = page.url.rsplit("/", 1)[-1]

    page.reload()
    expect(page.locator(".genui-block[data-state='ok'] .gu-metric-number")).to_have_text("2,940", timeout=20000)
    expect(page.get_by_role("slider", name="人数")).to_have_value("60")

    response = page.request.post(
        stack.portal + "/api/v1/shares", data={"kind": "conversation", "conversation_id": conv, "audience": "everyone"}
    )
    assert response.ok, response.text()
    shared = response.json()
    try:
        page.goto(stack.portal + "/s/" + shared["id"])
        expect(page.locator(".genui-block[data-state='ok'] .gu-metric-number")).to_have_text("2,940", timeout=20000)
        context.add_init_script("window.print = () => { window.genuiPrinted = true; }")
        page.goto(stack.portal + "/print/" + conv)
        page.wait_for_function("() => window.genuiPrinted === true")
        expect(page.locator(".genui-block[data-state='ok'] .gu-metric-number")).to_have_text("2,940")
    finally:
        page.request.delete(stack.portal + "/api/v1/shares/" + shared["id"])


def _csp(response) -> dict[str, str]:
    return dict(([*d.strip().split(" ", 1), ""])[:2] for d in response.headers["content-security-policy"].split(";") if d.strip())


def test_portal_pages_and_the_frame_route_send_their_csp(logged_in, stack) -> None:
    page = logged_in
    main = _csp(page.request.get(stack.portal + "/"))
    assert re.fullmatch(r"'self' 'nonce-[\w+/=]+' 'strict-dynamic' 'wasm-unsafe-eval'", main["script-src"]), main
    assert main["object-src"] == "'none'" and main["frame-ancestors"] == "'none'"
    run = _csp(page.request.get(stack.portal + "/frame/genui-run"))
    assert run["default-src"] == "'none'" and "connect-src" not in run and run["frame-ancestors"] == "'self'"
    preview = _csp(page.request.get(stack.portal + "/frame/genui-preview"))
    assert re.fullmatch(r"'sha256-[\w+/=]+'", preview["script-src"]), preview  # only the shell: no page script runs
