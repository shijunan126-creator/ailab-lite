"""Admin skill governance through the production Portal and API."""

import pytest
from playwright.sync_api import expect

from .conftest import login

pytestmark = pytest.mark.e2e

PPT = "演示文稿（PPT）"
PERSONAL = "个人技能"
STATE_LABEL = {"allowed": "允许", "review": "需审核", "disabled": "禁用"}


def set_policy(page, label: str, state: str) -> None:
    # The select is optimistic only in the UI; wait for the PUT itself, not for the control to become enabled.
    with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith("/v1/skills/admin/policies")) as put:
        page.get_by_role("button", name=label, exact=True).click()
        page.get_by_role("listbox", name=label, exact=True).get_by_role("option", name=STATE_LABEL[state], exact=True).click()
    assert put.value.ok
    expect(page.get_by_role("button", name=label, exact=True)).to_have_text(STATE_LABEL[state])


def test_admin_skill_policy_persists(page, stack):
    login(page, stack, path="/admin")
    page.get_by_role("tab", name="技能", exact=True).click()
    expect(page.get_by_role("button", name=PPT, exact=True)).to_have_text(STATE_LABEL["allowed"])
    set_policy(page, PPT, "disabled")
    page.reload()
    expect(page.get_by_role("button", name=PPT, exact=True)).to_have_text(STATE_LABEL["disabled"])
    set_policy(page, PERSONAL, "review")
    page.reload()
    expect(page.get_by_role("button", name=PERSONAL, exact=True)).to_have_text(STATE_LABEL["review"])
    set_policy(page, PPT, "allowed")
    set_policy(page, PERSONAL, "allowed")


@pytest.mark.parametrize("mobile", [False, True])
def test_admin_has_one_file_tab_and_one_panel(page, stack, mobile):
    if mobile:
        page.set_viewport_size({"width": 390, "height": 844})
    login(page, stack, path="/admin")
    tab = page.get_by_role("tab", name="文件", exact=True)
    expect(tab).to_have_count(1)
    tab.click()
    expect(tab).to_have_attribute("aria-selected", "true")
    expect(page.get_by_role("heading", name="文件溯源", exact=True)).to_have_count(1)
    expect(page.get_by_role("textbox", name="搜索文件", exact=True)).to_have_count(1)
    page.reload()
    expect(page.get_by_role("tab", name="文件", exact=True)).to_have_count(1)
    expect(page.get_by_role("textbox", name="搜索文件", exact=True)).to_have_count(1)
