"""Typing "@" in the composer offers library files; picking one names it in the text and sends it as an attachment."""

from __future__ import annotations

import json
import uuid

import pytest

from .test_library_ui import _shot, _upload

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("width", [1280, 390])
def test_at_mention_attaches_a_library_file(logged_in, stack, tmp_path, width) -> None:
    page = logged_in
    tag = uuid.uuid4().hex[:6]
    wanted = f"QuarterlyReport-{tag}.txt"
    _upload(page, stack, tmp_path, wanted, b"revenue up")
    _upload(page, stack, tmp_path, f"notes-{tag}.txt", b"other")
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(stack.portal + "/")

    box = page.get_by_role("textbox", name="消息")
    box.click()
    box.press_sequentially("对比一下 @report")
    menu = page.get_by_role("listbox", name="引用文件")
    option = menu.get_by_role("option", name=wanted)
    option.wait_for(timeout=10000)
    assert option.locator("mark").first.inner_text().lower() == "report"
    assert menu.get_by_role("option", name=f"notes-{tag}.txt").count() == 0
    _shot(page, f"mention-menu-{width}")

    box.press("Enter")
    menu.wait_for(state="detached")
    assert box.inner_text().strip() == f"对比一下 {wanted}"
    page.locator(".composer-attachments .attach-chip", has_text=wanted).wait_for()

    # Escape keeps a typed "@" as text and closes the list
    box.press_sequentially(" @no")
    page.get_by_role("listbox", name="引用文件").wait_for()
    box.press("Escape")
    page.get_by_role("listbox", name="引用文件").wait_for(state="detached")

    file_id = next(f["id"] for f in page.request.get(stack.portal + "/api/v1/files").json() if f["filename"] == wanted)
    with page.expect_request(lambda r: r.method == "POST" and "file_ids" in (r.post_data or "")) as sent:
        page.get_by_role("button", name="发送").click()
    assert json.loads(sent.value.post_data)["file_ids"] == [file_id]
