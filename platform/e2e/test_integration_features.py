"""Feature acceptance through the production Portal, real API/queues and a deterministic model."""

from __future__ import annotations

import re
import uuid

import httpx
import pytest
from playwright.sync_api import expect

from .scripted_agent import FEATURE_ANSWER, FEATURE_STREAM, FEATURE_TITLE
from .test_portal import _send, _start_conversation, _wait_for_turns

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def start_in_chat_mode(logged_in):
    logged_in.get_by_role("radio", name="对话", exact=True).click()


def _prompt(label):
    return f"集成版本{label}-{uuid.uuid4().hex[:8]}"


def _menu(page, conv):
    row = page.locator(f".conv-item:has(a[href='/c/{conv}'])")
    row.hover()
    row.locator(".conv-more").click()


def test_full_reasoning_and_early_title_while_answer_streams(logged_in, stack):
    page = logged_in
    # Detect an accidental document reload while the first title arrives via SSE.
    page.evaluate("window.integrationDocument = 'same-document'")
    _start_conversation(page, FEATURE_STREAM)
    try:
        expect(page.locator(".assistant-body > .md")).to_have_text(FEATURE_ANSWER, timeout=15000)
        expect(page.locator(".turn-meta")).to_have_count(0)
        assert page.locator(".pr-thought-body").count() == 0
        page.locator(".pr-thought > button").first.click()
        body = page.locator(".pr-thought-body")
        expect(body).to_be_visible()
        expect(body).to_contain_text("分析问题")
        expect(body).to_contain_text("组织回答")
        expect(body).to_contain_text("先确认目标和约束")
        assert body.locator(".collapse").count() == 0
        expect(page.locator(".conv-item.on .conv-title")).to_have_text("新对话")
        httpx.post(stack.agent + "/e2e/release-title", timeout=5).raise_for_status()
        expect(page.locator(".conv-item.on .conv-title")).to_have_text(FEATURE_TITLE, timeout=15000)
        assert "**" not in page.locator(".conv-item.on .conv-title").inner_text()
        assert page.evaluate("window.integrationDocument") == "same-document"
        assert page.locator(".work-process .md").filter(has_text=FEATURE_ANSWER).count() == 0
    finally:
        httpx.post(stack.agent + "/e2e/release-title", timeout=5).raise_for_status()
        httpx.post(stack.agent + "/e2e/release-stream", timeout=5).raise_for_status()
    _wait_for_turns(page, 1)


def test_retry_versions_and_selected_version_survive_reload(logged_in):
    page = logged_in
    _start_conversation(page, _prompt("重试"))
    _wait_for_turns(page, 1)
    original = page.locator(".assistant-body > .md").inner_text()
    page.get_by_role("button", name="重新生成", exact=True).click()
    expect(page.locator(".versions-n")).to_have_text("2/2", timeout=20000)
    expect(page.locator(".assistant-body > .md")).not_to_have_text(original)
    page.get_by_role("button", name="上一个版本", exact=True).click()
    expect(page.locator(".versions-n")).to_have_text("1/2")
    expect(page.locator(".assistant-body > .md")).to_have_text(original)
    page.reload()
    expect(page.locator(".versions-n")).to_have_text("1/2")
    expect(page.locator(".assistant-body > .md")).to_have_text(original)


def test_edit_first_turn_creates_path_and_switch_restores_descendants(logged_in):
    page = logged_in
    first, second, edited = _prompt("原问"), _prompt("追问"), _prompt("编辑")
    _start_conversation(page, first)
    _wait_for_turns(page, 1)
    _send(page, second)
    _wait_for_turns(page, 2)
    page.get_by_role("button", name="编辑消息", exact=True).first.click()
    page.get_by_role("textbox", name="编辑消息", exact=True).fill(edited)
    page.locator(".edit-box").get_by_role("button", name="发送", exact=True).click()
    expect(page.locator(".versions-n")).to_have_text("2/2", timeout=20000)
    expect(page.locator(".turn")).to_have_count(1)
    expect(page.locator(".msg.user .bubble")).to_have_text(edited)
    page.get_by_role("button", name="上一个版本", exact=True).click()
    expect(page.locator(".turn")).to_have_count(2)
    expect(page.locator(".msg.user .bubble")).to_have_text([first, second])


def test_fork_first_turn_and_work_only_menu(logged_in, stack):
    """A turn branches as what the conversation was up to it: chat turns offer both, a work conversation only work."""
    page = logged_in
    first = _prompt("分支")
    original = _start_conversation(page, first)
    _wait_for_turns(page, 1)
    _send(page, _prompt("后续"))
    _wait_for_turns(page, 2)
    page.get_by_role("button", name="从这里分支", exact=True).first.click()
    expect(page.get_by_role("menuitem", name="分支为工作", exact=True)).to_be_visible()
    page.get_by_role("menuitem", name="分支为对话", exact=True).click()
    page.wait_for_url(lambda url: "/c/" in url and not url.endswith(original))
    expect(page.locator(".turn")).to_have_count(1)
    expect(page.locator(".msg.user .bubble")).to_have_text(first)
    expect(page.locator(".crumb-fork")).to_be_visible()
    # a work conversation has worked in files: it branches only into work
    page.goto(stack.portal + "/")
    page.get_by_role("radio", name="工作", exact=True).click()
    _start_conversation(page, _prompt("工作"))
    _wait_for_turns(page, 1)
    page.get_by_role("button", name="从这里分支", exact=True).last.click()
    expect(page.get_by_role("menuitem", name="分支为对话", exact=True)).to_have_count(0)
    expect(page.get_by_role("menuitem", name="分支为工作", exact=True)).to_be_visible()


@pytest.mark.parametrize("shortcut", ["Meta+k", "Control+k"])
def test_pin_and_search_earlier_prompt_with_unicode_highlight(logged_in, stack, shortcut):
    page = logged_in
    word = "检索词" + uuid.uuid4().hex[:8]
    first = f"集成版本😀 {word} 原始问题"
    conv = _start_conversation(page, first)
    _wait_for_turns(page, 1)
    _send(page, _prompt("后续"))
    _wait_for_turns(page, 2)
    _menu(page, conv)
    page.get_by_role("menuitem", name="置顶", exact=True).click()
    pinned = page.get_by_role("navigation", name="置顶对话").locator(f"a[href='/c/{conv}']")
    expect(pinned).to_be_visible()
    page.reload()
    expect(pinned).to_be_visible()
    page.goto(stack.portal + "/")
    # The account chip appears after client effects; SSR navigation alone does not install shortcuts.
    expect(page.locator(".user-chip")).to_be_visible()
    page.keyboard.press(shortcut)
    dialog = page.locator(".search-dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_role("textbox").fill(word)
    expect(dialog.locator("mark")).to_have_text(word)
    dialog.get_by_role("textbox").press("Enter")
    page.wait_for_url(re.compile(rf"/c/{conv}\?run="))
    expect(page.locator(".msg.user .bubble").first).to_have_text(first)


def test_slash_selects_skill_template_and_work_mode(logged_in):
    page = logged_in
    page.get_by_role("textbox", name="消息", exact=True).fill("/")
    expect(page.locator(".slash-menu")).to_be_visible()
    page.get_by_role("textbox", name="消息", exact=True).press("Enter")
    expect(page.locator(".slash-menu")).to_have_count(0)
    expect(page.get_by_role("textbox", name="消息", exact=True).locator(".skill-chip")).to_be_visible()


def test_library_page_citation_opens_real_file_viewer(logged_in, stack, tmp_path):
    from pypdf import PdfWriter

    page = logged_in
    path = tmp_path / "a.pdf"
    doc = PdfWriter()
    for _ in range(2):
        doc.add_blank_page(width=200, height=300)
    doc.write(path)
    page.goto(stack.portal + "/files")
    page.set_input_files("input[type=file]", str(path))
    row = page.locator(".lib-row", has_text="a.pdf")
    row.get_by_role("link", name="下载 a.pdf", exact=True).wait_for(timeout=30000)
    files = page.request.get(stack.portal + "/api/v1/files").json()
    file = next(f for f in files if f["filename"] == "a.pdf")
    try:
        page.goto(stack.portal + "/")
        _start_conversation(page, f"集成页码引用 {file['id']}")
        _wait_for_turns(page, 1)
        chip = page.locator(".assistant-body > .md .md-file")
        expect(chip).to_contain_text("第2页")
        chip.click()
        expect(page.locator(".viewer")).to_be_visible()
        expect(page.locator(".viewer")).to_contain_text("a.pdf")
    finally:
        page.request.delete(stack.portal + f"/api/v1/files/{file['id']}")


def test_schedule_create_run_and_five_task_limit(logged_in, stack):
    page = logged_in
    ids = []
    page.goto(stack.portal + "/schedules")
    try:
        for i in range(5):
            title = f"集成任务-{uuid.uuid4().hex[:8]}"
            page.get_by_role("button", name="新建", exact=True).click()
            dialog = page.get_by_role("dialog")
            dialog.get_by_label("名称", exact=True).fill(title)
            dialog.get_by_label("要做什么", exact=True).fill(_prompt("定时"))
            dialog.get_by_role("button", name="模式", exact=True).click()
            page.get_by_role("listbox", name="模式", exact=True).get_by_role("option", name="对话", exact=True).click()
            with page.expect_response(lambda r: r.url.endswith("/api/v1/schedules") and r.request.method == "POST") as response:
                dialog.get_by_role("button", name="保存", exact=True).click()
            assert response.value.status == 201, response.value.text()
            ids.append(response.value.json()["id"])
            expect(dialog).to_have_count(0)
            row = page.locator(".mrow", has_text=title)
            expect(row).to_be_visible()
            if i == 0:
                row.get_by_role("button", name=f"更多操作 {title}", exact=True).click()
                page.get_by_role("menuitem", name="立即运行", exact=True).click()
                link = row.locator(".mrow-trail a")
                expect(link).to_be_visible(timeout=15000)
                link.click()
                page.wait_for_url(re.compile(r"/c/cnv_"))
                _wait_for_turns(page, 1)
                page.goto(stack.portal + "/schedules")
        expect(page.get_by_role("button", name="新建", exact=True)).to_be_disabled()
        refused = page.request.post(
            stack.portal + "/api/v1/schedules",
            data={
                "title": "第六条",
                "prompt": "不应创建",
                "mode": "chat",
                "timezone": "UTC",
                "schedule": {"kind": "daily", "time": "09:00"},
            },
        )
        assert refused.status == 422, refused.text()
    finally:
        for sid in ids:
            assert page.request.delete(stack.portal + f"/api/v1/schedules/{sid}").status == 204


def test_context_meter_draft_does_not_resize_composer(logged_in):
    page = logged_in
    _start_conversation(page, _prompt("上下文"))
    _wait_for_turns(page, 1)
    composer = page.locator(".composer-box")
    height = composer.bounding_box()["height"]
    page.get_by_role("textbox", name="消息", exact=True).fill("一行简短的草稿")
    expect(page.locator(".ctx-meter")).to_contain_text("≈")
    expect(page.locator(".ctx-meter")).to_have_attribute("aria-label", re.compile("本条约"))
    assert abs(composer.bounding_box()["height"] - height) < 1


def test_admin_context_toggle_persists(logged_in, stack):
    page = logged_in
    original = page.request.get(stack.portal + "/api/v1/admin/settings/context").json()
    try:
        page.goto(stack.portal + "/admin")
        page.get_by_role("tab", name="上下文", exact=True).click()
        switch = page.get_by_role("switch", name="自动压缩上下文", exact=True)
        expect(switch).to_have_attribute("aria-checked", str(original["auto_compact"]).lower())
        switch.click()
        with page.expect_response(lambda r: r.url.endswith("/api/v1/admin/settings/context") and r.request.method == "PUT") as response:
            page.get_by_role("button", name="保存", exact=True).click()
        assert response.value.ok
        page.reload()
        page.get_by_role("tab", name="上下文", exact=True).click()
        expect(switch).to_have_attribute("aria-checked", str(not original["auto_compact"]).lower())
    finally:
        assert page.request.put(
            stack.portal + "/api/v1/admin/settings/context",
            data={"auto_compact": original["auto_compact"], "threshold_ratio": original["threshold_ratio"]},
        ).ok


def test_export_prints_all_turns_of_selected_path(logged_in, context):
    page = logged_in
    context.add_init_script("window.print = () => { window.integrationPrinted = true; }")
    first, second = _prompt("打印一"), _prompt("打印二")
    conv = _start_conversation(page, first)
    _wait_for_turns(page, 1)
    _send(page, second)
    _wait_for_turns(page, 2)
    # Retry leaves an inactive sibling that must not be printed.
    page.get_by_role("button", name="重新生成", exact=True).click()
    expect(page.locator(".versions-n")).to_have_text("2/2", timeout=20000)
    answers = page.locator(".assistant-body > .md").all_inner_texts()
    _menu(page, conv)
    expect(page.get_by_role("menuitem", name="导出 PDF", exact=True)).to_have_count(0)
    page.get_by_role("menuitem", name="分享", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_role("button", name="创建链接", exact=True)).to_be_visible()
    with page.expect_popup() as popup:
        dialog.get_by_role("button", name="导出 PDF", exact=True).click()
    printed = popup.value
    printed.wait_for_url(f"**/print/{conv}")
    expect(printed.locator(".print-turn")).to_have_count(2)
    expect(printed.locator(".print-q")).to_have_text([first, second])
    expect(printed.locator(".print-a")).to_have_text(answers)
    printed.wait_for_function("window.integrationPrinted === true")
    printed.close()


def test_mobile_composer_and_fixed_library_selection(logged_in, stack, tmp_path):
    page = logged_in
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("button", name="收起侧栏", exact=True).click()
    _start_conversation(page, _prompt("手机"))
    _wait_for_turns(page, 1)
    assert page.evaluate("document.documentElement.scrollWidth === innerWidth")
    area = page.get_by_role("textbox", name="消息", exact=True)
    assert area.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 16
    name = f"mobile-{uuid.uuid4().hex[:8]}.txt"
    path = tmp_path / name
    path.write_text("mobile selection", encoding="utf-8")
    page.goto(stack.portal + "/files")
    page.set_input_files("input[type=file]", str(path))
    row = page.locator(".lib-row", has_text=name)
    row.get_by_role("link", name=f"下载 {name}", exact=True).wait_for(timeout=30000)
    row.get_by_role("checkbox").click()
    bar = page.locator(".select-bar")
    expect(bar).to_be_visible()
    expect(bar).to_have_css("position", "fixed")
    # Wait for the entrance transform, then assert every edge against the visual viewport.
    page.wait_for_function("""() => {
        const r = document.querySelector('.select-bar').getBoundingClientRect();
        return r.x >= 0 && r.right <= innerWidth && r.y >= 0 && r.bottom <= innerHeight;
    }""")
    page.screenshot(path=str(stack.log_dir / "integration-mobile-selection.png"))
