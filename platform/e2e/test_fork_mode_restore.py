"""A branch's mode survives another conversation/browser preference and a reload."""

import pytest
from playwright.sync_api import expect

from .test_portal import _send, _start_conversation, _wait_for_turns

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("mode", ["chat", "work"])
@pytest.mark.parametrize("mobile", [False, True])
def test_fork_restores_its_mode_before_sending(logged_in, stack, mode, mobile):
    page = logged_in
    if mobile:
        page.set_viewport_size({"width": 390, "height": 844})
        page.reload()
    page.get_by_role("radio", name="对话", exact=True).click()
    original = _start_conversation(page, "分支模式继承验证")
    _wait_for_turns(page, 1)
    page.get_by_role("button", name="从这里分支", exact=True).first.click()
    label = "工作" if mode == "work" else "对话"
    page.get_by_role("menuitem", name="分支为" + label, exact=True).click()
    page.wait_for_url(lambda url: "/c/" in url and not url.endswith(original))
    branch_url = page.url
    expect(page.get_by_role("radio", name=label, exact=True)).to_have_attribute("aria-checked", "true")
    # A different page/session overwrites the browser preference. Opening this branch must restore its own mode.
    page.evaluate("value => localStorage.setItem('ailab.mode', value)", "chat" if mode == "work" else "work")
    page.reload()
    expect(page.get_by_role("radio", name=label, exact=True)).to_have_attribute("aria-checked", "true")
    _send(page, "继续此分支")
    _wait_for_turns(page, 2)
    detail = page.request.get(stack.portal + "/api/v1/conversations/" + branch_url.rsplit("/", 1)[1]).json()
    assert detail["turns"][-1]["options"]["mode"] == mode
    # A deliberate switch remains possible and survives detail refreshes and reopening.
    other = "对话" if mode == "work" else "工作"
    page.get_by_role("radio", name=other, exact=True).click()
    _send(page, "按选择的新模式继续")
    _wait_for_turns(page, 3)
    page.reload()
    expect(page.get_by_role("radio", name=other, exact=True)).to_have_attribute("aria-checked", "true")
