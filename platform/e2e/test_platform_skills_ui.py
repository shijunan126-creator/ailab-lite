"""Production UI + API + Worker: an admin publishes a platform skill, a user adds it from 技能广场 and follows its updates."""

import uuid

import httpx
import pytest
from playwright.sync_api import expect

from .test_memory_skills import auth

pytestmark = pytest.mark.e2e


def skill_md(name: str, body: str) -> str:
    return f"---\nname: {name}\ntitle: 会议纪要规范\ndescription: 按公司格式整理会议纪要\n---\n# 步骤\n{body}\n"


def test_admin_publishes_and_users_follow_platform_skill(logged_in, stack, tmp_path) -> None:
    page = logged_in
    name = f"minutes-{uuid.uuid4().hex[:6]}"
    first = tmp_path / "v1" / "SKILL.md"
    first.parent.mkdir()
    first.write_text(skill_md(name, "第一版：先写结论。"), encoding="utf-8")

    # admin: create with its file; the Worker scans it and it is published
    page.goto(stack.portal + "/admin?t=market")
    page.get_by_role("button", name="新建", exact=True).click()
    dialog = page.get_by_role("dialog", name="新建平台技能")
    dialog.get_by_label("名称").fill("会议纪要规范")
    dialog.get_by_label("标识").fill(name)
    dialog.get_by_label("分类").fill("写作")
    dialog.get_by_label("技能文件").set_input_files(str(first))
    dialog.get_by_role("button", name="创建", exact=True).click()
    row = page.locator(".mrow", has_text="会议纪要规范")
    expect(row).to_be_visible()
    expect(row.locator(".state", has_text="未发布")).to_have_count(0, timeout=60000)

    try:
        # user: add it from the square; it shows under 广场技能
        page.goto(stack.portal + "/skills?tab=market")
        card = page.locator(".market-card", has_text="会议纪要规范")
        card.get_by_role("button", name="添加", exact=True).click()
        expect(card.get_by_role("button", name="已添加", exact=True)).to_be_disabled()
        page.get_by_role("tab", name="我的技能", exact=True).click()
        mine = page.locator(".msection", has_text="广场技能").locator(".mrow", has_text="会议纪要规范")
        expect(mine).to_be_visible()
        assert mine.locator("button[role=switch]").get_attribute("aria-checked") == "true"

        # admin uploads a new version: the unmodified copy follows it
        second = tmp_path / "v2" / "SKILL.md"
        second.parent.mkdir()
        second.write_text(skill_md(name, "第二版：先写待办。"), encoding="utf-8")
        page.goto(stack.portal + "/admin?t=market")
        row.get_by_role("button", name="更多操作").click()
        with page.expect_file_chooser() as chooser:
            page.get_by_role("menuitem", name="上传新版本", exact=True).click()
        chooser.value.set_files(str(second))
        page.locator(".mrow", has_text="会议纪要规范").get_by_role("button", name="更多操作").click()
        page.get_by_role("menuitem", name="版本", exact=True).click()
        versions = page.get_by_role("dialog")
        expect(versions.locator(".mrow", has_text="v2").locator(".state", has_text="当前")).to_be_visible(timeout=60000)
        page.keyboard.press("Escape")

        page.goto(stack.portal + "/skills")
        page.locator(".msection", has_text="广场技能").get_by_role("button", name="查看 会议纪要规范").click()
        expect(page.get_by_role("dialog").locator("textarea")).to_have_value(skill_md(name, "第二版：先写待办。"), timeout=10000)
        page.keyboard.press("Escape")

        # removing it takes it out of the user's skills and offers it again in the square
        mine.get_by_role("button", name="更多操作").click()
        page.get_by_role("menuitem", name="移除", exact=True).click()
        page.get_by_role("button", name="移除", exact=True).click()
        expect(mine).to_have_count(0)
    finally:
        headers = auth(stack)
        listed = httpx.get(stack.api + "/v1/admin/platform-skills", headers=headers).json()["items"]
        for item in listed:
            if item["name"] == name:
                httpx.delete(stack.api + f"/v1/admin/platform-skills/{item['id']}", headers=headers)
