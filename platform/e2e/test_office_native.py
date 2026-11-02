"""Real uploads, bounded Worker indices, native frame references and Agent client calls."""

import re
import time

import httpx
import pytest
from playwright.sync_api import expect

from .conftest import PLATFORM
from .test_portal import _send, _start_conversation, _wait_for_turns

pytestmark = pytest.mark.e2e
FIXTURES = PLATFORM.parent / "vendor/office-suite/runtime/fixtures"


def _index(page, stack, file_id):
    end = time.monotonic() + 30
    value = None
    while time.monotonic() < end:
        response = page.request.get(f"{stack.portal}/api/v1/files/{file_id}/document")
        assert response.ok, response.text()
        value = response.json()
        if value.get("status") == "ready":
            assert value["native"], value
            return value
        page.wait_for_timeout(150)
    raise AssertionError(value)


def _frame(page, name):
    frame = page.frame_locator(f'iframe.office-frame[title="{name}"]')
    expect(frame.locator(".canvas")).to_be_visible(timeout=30000)
    return frame


def _select(page, frame, kind):
    if kind == "xlsx":
        canvas = frame.locator("canvas").first
        expect(canvas).to_be_visible(timeout=20000)
        box = canvas.bounding_box()
        assert box
        # The first occupied range in the real Revenue worksheet, using actual pointer events.
        page.mouse.move(box["x"] + 80, box["y"] + 45)
        page.mouse.down()
        page.mouse.move(box["x"] + 280, box["y"] + 90, steps=12)
        page.mouse.up()
    else:
        selector = {"docx": '.docx [data-at="p:2"]', "pdf": ".textLayer span", "pptx": ".pptx-slide svg text"}[kind]
        text = frame.locator(selector).first
        expect(text).to_be_visible(timeout=25000)
        text.dblclick()
    add = frame.get_by_role("button", name=re.compile("^添加(到对话)?$"))
    expect(add).to_be_visible(timeout=10000)
    add.click()
    expect(page.locator(".composer-box .quote-chip")).to_be_visible()


@pytest.mark.parametrize("kind,name", [("docx", "report.docx"), ("xlsx", "budget.xlsx"), ("pptx", "launch.pptx"), ("pdf", "cjk.pdf")])
def test_chat_upload_native_select_quote_send_and_reveal(logged_in, stack, kind, name):
    page = logged_in
    page.get_by_role("radio", name="对话", exact=True).click()
    page.set_input_files(".composer-box input[type=file]", str(FIXTURES / name))
    _start_conversation(page, "请确认已收到文件")
    _wait_for_turns(page, 1)
    chip = page.locator(".turn .attach-chip.open").filter(has_text=name).first
    chip.click()
    frame = _frame(page, name)
    source = page.request.get(stack.portal + "/api/v1/files").json()
    file = next(item for item in source if item["filename"] == name)
    _index(page, stack, file["id"])
    _select(page, frame, kind)
    quote = page.locator(".composer-box .quote-chip-text").inner_text()
    assert quote.strip()
    _send(page, "请解释这段引用")
    _wait_for_turns(page, 2)
    sent = page.locator(".turn .quote-chip").last
    expect(sent).to_contain_text(quote)
    sent.click()
    expect(_frame(page, name).locator(".agent-mark")).to_be_visible(timeout=10000)
    if kind == "docx":
        results = []
        page.on(
            "response",
            lambda response: results.append(response) if "/client-calls/" in response.url and response.url.endswith("/result") else None,
        )
        _send(page, "原生控制 " + file["id"])
        _wait_for_turns(page, 3)
        assert results and all(r.ok for r in results)
        assert "unavailable" not in page.locator(".turn").last.inner_text()
        expect(page.locator(".turn").last).to_contain_text("原生截图结果")


def test_live_work_file_native_agent_reveal_and_capture(logged_in, stack):
    page = logged_in
    results = []
    page.on(
        "response",
        lambda response: results.append(response) if "/client-calls/" in response.url and response.url.endswith("/result") else None,
    )
    page.get_by_role("radio", name="工作", exact=True).click()
    _start_conversation(page, "原生工作区验收")
    try:
        frame = _frame(page, "draft.docx")
        expect(frame.locator('.docx [data-at="p:2"]')).to_be_visible(timeout=25000)
        expect(frame.locator(".agent-mark")).to_be_visible(timeout=10000)
        page.wait_for_timeout(900)  # The viewer state uses a 600 ms debounce.
    finally:
        httpx.post(stack.agent + "/e2e/office-release", timeout=5).raise_for_status()
    _wait_for_turns(page, 1)
    assert results and all(r.ok for r in results)
    assert "unavailable" not in page.locator(".turn").last.inner_text()
    expect(page.locator(".turn").last).to_contain_text("原生截图结果")
