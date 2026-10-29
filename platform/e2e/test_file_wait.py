"""Automatic dependency suspension remains live and cancellable in both layouts."""

import pytest
from playwright.sync_api import expect

from .conftest import login
from .test_conversation_ui import seed

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("width", [1280, 390])
def test_file_wait_has_no_manual_resume_and_can_be_stopped(page, stack, width):
    page.set_viewport_size({"width": width, "height": 900})
    conv = seed(stack, active=False)
    waiting = True

    def conversation(route):
        response = route.fetch()
        data = response.json()
        if waiting:
            run = data["turns"][-1]
            run.update(status="interrupted", resumable=False, error="正在准备附件")
            run["result"] = {**run.get("result", {}), "file_wait": {"action": "extract-text"}}
        route.fulfill(response=response, json=data)

    def cancel(route):
        nonlocal waiting
        waiting = False
        route.fulfill(json={"status": "canceled"})

    login(page, stack)
    page.route("**/api/v1/conversations/*?*", conversation)
    page.route("**/api/v1/runs/*/cancel", cancel)
    page.goto(stack.portal + "/c/" + conv["id"])
    expect(page.get_by_text("正在准备附件…", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="继续", exact=True)).to_have_count(0)
    stop = page.get_by_role("button", name="停止", exact=True)
    expect(stop).to_be_visible()
    stop.click()
    expect(page.get_by_text("正在准备附件…", exact=True)).to_have_count(0)
