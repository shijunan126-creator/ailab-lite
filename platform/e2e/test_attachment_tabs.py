"""Attachment navigation regression; API fixtures test UI identity, not parser quality."""

import pytest

pytestmark = pytest.mark.e2e


def check_attachment_tabs(page, conversation_url):
    def conversation(route):
        response = route.fetch()
        data = response.json()
        data["turns"][0]["input_files"] = [
            {"file_id": "tab-fixture-a", "filename": "同名.txt"},
            {"file_id": "tab-fixture-b", "filename": "同名.txt"},
        ]
        route.fulfill(response=response, json=data)

    def file(route):
        if route.request.url.endswith("/text.txt"):
            route.fulfill(content_type="text/plain", body="second content" if "fixture-b" in route.request.url else "first content")
        else:
            route.fulfill(
                json={
                    "id": route.request.url.rsplit("/", 1)[-1],
                    "filename": "同名.txt",
                    "status": "ready",
                    "content_type": "text/plain",
                    "preview": {"status": "ready", "kind": "text", "text": "text.txt", "format": "plain"},
                }
            )

    page.route("**/api/v1/conversations/*?*", conversation)
    page.route("**/api/v1/files/tab-fixture-**", file)
    page.goto(conversation_url)
    chips = page.locator(".attach-chip.open")
    chips.nth(0).click()
    page.wait_for_selector(".preview-pane .viewer-plain:has-text('first content')")
    assert page.locator(".viewer").count() == 0
    mobile = page.viewport_size["width"] <= 760
    if mobile:
        page.get_by_role("button", name="收起预览").click()
    chips.nth(1).click()
    page.wait_for_selector(".preview-pane .viewer-plain:has-text('second content')")
    tabs = page.get_by_role("tab", name="同名.txt", exact=True)
    assert tabs.count() == 2
    tabs.nth(0).click()
    page.wait_for_selector(".preview-pane .viewer-plain:has-text('first content')")
    page.get_by_role("button", name="关闭 同名.txt", exact=True).nth(0).click()
    page.wait_for_selector(".preview-pane .viewer-plain:has-text('second content')")
    assert tabs.count() == 1
    page.keyboard.press("Escape")
    page.wait_for_selector(".preview-pane", state="detached")


@pytest.mark.parametrize("mobile", [False, True])
def test_sent_attachments_use_tabs_by_file_id(page, stack, mobile):
    from .conftest import login
    from .test_conversation_ui import seed

    if mobile:
        page.set_viewport_size({"width": 390, "height": 844})
    conv = seed(stack, active=False)
    login(page, stack)
    check_attachment_tabs(page, stack.portal + "/c/" + conv["id"])
