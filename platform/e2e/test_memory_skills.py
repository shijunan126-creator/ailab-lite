"""Production UI + API + Worker + Agent: editable skills and account memory."""

import re
import time
import uuid

import httpx
import pytest
from playwright.sync_api import expect

from .conftest import PASSWORD, USERNAME, login

pytestmark = pytest.mark.e2e


def auth(stack):
    response = httpx.post(stack.api + "/v1/auth/login", json={"username": USERNAME, "password": PASSWORD})
    return {"Authorization": "Bearer " + response.json()["token"]}


def reset_memory(stack, headers):
    current = httpx.get(stack.api + "/v1/memory", headers=headers).json()
    result = httpx.put(
        stack.api + "/v1/memory",
        headers=headers,
        json={
            "expected_revision": current["revision"],
            "enabled": True,
            "auto_save": True,
            "reference_history": True,
            "entries": [],
        },
    )
    assert result.status_code == 200


def put_facts(stack, headers, entries):
    current = httpx.get(stack.api + "/v1/memory", headers=headers).json()
    saved = httpx.put(stack.api + "/v1/memory", headers=headers, json={"expected_revision": current["revision"], "entries": entries})
    assert saved.status_code == 200, saved.text
    return saved.json()


def summary_dialog(page, name="你的记忆"):
    dialog = page.get_by_role("dialog", name=name)
    expect(dialog).to_be_visible()
    return dialog


def ask(dialog, text):
    box = dialog.get_by_role("textbox", name="修改记忆", exact=True)
    box.fill(text)
    box.press("Enter")


def test_memory_summary_is_written_changed_by_asking_and_undone(page, stack):
    headers = auth(stack)
    reset_memory(stack, headers)
    put_facts(stack, headers, [{"kind": "user", "content": "回答先给结论"}, {"content": "正在准备年度预算汇报"}])
    login(page, stack, path="/memory")
    page.get_by_role("button", name="你的记忆", exact=True).click()
    dialog = summary_dialog(page)
    # no summary yet: opening it writes one
    expect(dialog.locator(".memory-summary")).to_contain_text("回答先给结论", timeout=30000)
    ask(dialog, "记住我常驻上海")
    expect(dialog.locator(".memory-turn.ai").last).to_contain_text("已记住", timeout=30000)
    expect(dialog.locator(".memory-summary")).to_contain_text("我常驻上海")
    dialog.get_by_role("button", name="撤销", exact=True).click()
    expect(dialog.locator(".memory-turn-note")).to_have_text("已撤销")
    expect(dialog.locator(".memory-summary")).not_to_contain_text("我常驻上海")
    ask(dialog, "忘记预算汇报")
    expect(dialog.locator(".memory-turn.ai").last).to_contain_text("已忘记 1 条", timeout=30000)
    expect(dialog.locator(".memory-summary")).not_to_contain_text("预算汇报")
    expect(dialog.get_by_role("button", name="撤销", exact=True)).to_have_count(0)  # forgetting is final
    assert [e["content"] for e in httpx.get(stack.api + "/v1/memory", headers=headers).json()["entries"]] == ["回答先给结论"]
    dialog.get_by_role("button", name="立即整理").click()
    # other E2E tests leave conversations behind, so this may start a real consolidation instead of "已是最新"
    expect(dialog.get_by_role("button", name=re.compile("^(立即整理|已是最新)$"))).to_be_enabled(timeout=30000)
    expect(dialog.locator(".memory-summary")).to_contain_text("回答先给结论")
    dialog.get_by_role("button", name="关闭", exact=True).click()
    expect(page.locator(".memory-preview")).to_contain_text("回答先给结论")
    page.get_by_role("switch", name="自动记忆", exact=True).click()
    expect(page.get_by_role("switch", name="自动记忆", exact=True)).to_have_attribute("aria-checked", "false")
    page.reload()
    expect(page.get_by_role("switch", name="自动记忆", exact=True)).to_have_attribute("aria-checked", "false")
    memory = httpx.get(stack.api + "/v1/memory", headers=headers).json()
    assert memory["auto_save"] is False and len(memory["entries"]) == 1
    page.set_viewport_size({"width": 390, "height": 844})
    if page.get_by_role("button", name="收起侧栏", exact=True).is_visible():
        page.get_by_role("button", name="收起侧栏", exact=True).click()
    page.screenshot(path=str(stack.log_dir / "memory-mobile.png"), full_page=True, animations="disabled")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    reset_memory(stack, headers)


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_agent_memory_is_visible_in_account_page(page, stack, mode):
    headers = auth(stack)
    reset_memory(stack, headers)
    created = httpx.post(stack.api + "/v1/runs", headers=headers, json={"prompt": "验证账号记忆工具", "mode": mode}).json()
    login(page, stack, path="/memory")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        run = httpx.get(stack.api + "/v1/runs/" + created["id"], headers=headers).json()
        if run["status"] in {"succeeded", "failed", "canceled"}:
            break
        time.sleep(0.1)
    assert run["status"] == "succeeded", run
    assert httpx.get(stack.api + "/v1/memory", headers=headers).json()["entries"][0]["content"] == "验收偏好：回答保持简洁"
    page.reload()
    page.get_by_role("button", name="你的记忆", exact=True).click()
    expect(summary_dialog(page).locator(".memory-summary")).to_contain_text("验收偏好：回答保持简洁", timeout=30000)
    run = httpx.get(stack.api + "/v1/runs/" + created["id"], headers=headers).json()
    assert not run.get("sandbox_id")
    reset_memory(stack, headers)


def test_skill_create_edit_and_scanned_version(page, stack):
    name = "browser-" + uuid.uuid4().hex[:8]
    content = f"---\nname: {name}\ntitle: 浏览器验收技能\ndescription: 验证在线编辑\n---\n# 做法\n先阅读材料。\n"
    login(page, stack, path="/skills")
    page.get_by_role("button", name="新建", exact=True).click()
    editor = page.get_by_role("dialog")
    editor.get_by_role("textbox").fill(content)
    editor.get_by_role("button", name="保存", exact=True).click()
    row = page.locator(".mrow").filter(has_text="浏览器验收技能")
    row.get_by_role("button", name="编辑 浏览器验收技能", exact=True).wait_for(timeout=60000)
    expect(row.get_by_role("button", name="编辑 浏览器验收技能", exact=True)).to_be_enabled(timeout=60000)
    row.get_by_role("button", name="编辑 浏览器验收技能", exact=True).click()
    expect(editor.get_by_role("tree", name="技能文件")).to_be_visible()
    editor.get_by_role("textbox", name="SKILL.md").fill(content + "第二步校验结果。\n")
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/confirm")) as saved:
        editor.get_by_role("button", name="保存新版本", exact=True).click()
    fid = saved.value.json()["file_id"]
    expect(editor).to_have_count(0)
    headers = auth(stack)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        file = httpx.get(stack.api + f"/v1/files/{fid}", headers=headers).json()
        if file["status"] != "pending":
            break
        time.sleep(0.1)
    assert file["status"] == "ready", file
    page.reload()
    row.get_by_role("button", name="编辑 浏览器验收技能", exact=True).click()
    expect(editor.get_by_role("textbox", name="SKILL.md")).to_have_value(content + "第二步校验结果。\n")
    page.screenshot(path=str(stack.log_dir / "skill-edit.png"), animations="disabled")
    editor.get_by_role("button", name="关闭", exact=True).filter(has_text="关闭").click()
    headers = auth(stack)
    skill = next(s for s in httpx.get(stack.api + "/v1/skills", headers=headers).json() if s["name"] == name)
    assert len(httpx.get(stack.api + f"/v1/files/{skill['file_id']}/versions", headers=headers).json()) == 2
    assert httpx.delete(stack.api + f"/v1/skills/files/{skill['file_id']}", headers=headers).status_code == 204


@pytest.mark.parametrize("width", [1280, 390])
def test_project_memory_mode_moves_and_merges_facts(page, stack, width):
    headers = auth(stack)
    reset_memory(stack, headers)
    project = httpx.post(stack.api + "/v1/projects", headers=headers, json={"name": "记忆验收" + uuid.uuid4().hex[:6]}).json()
    facts = [{"kind": "user", "content": "回答先给结论"}, {"content": "季报口径以财务部为准", "project_id": project["id"]}]
    put_facts(stack, headers, facts)
    page.set_viewport_size({"width": width, "height": 900})
    login(page, stack, path=f"/p/{project['id']}")
    if width == 390 and page.get_by_role("button", name="收起侧栏", exact=True).is_visible():
        page.get_by_role("button", name="收起侧栏", exact=True).click()
    page.get_by_role("button", name="项目操作", exact=True).click()
    page.get_by_role("menuitem", name="记忆", exact=True).click()
    dialog = summary_dialog(page, "项目记忆")
    expect(dialog.locator(".memory-summary")).to_contain_text("季报口径以财务部为准", timeout=30000)
    expect(dialog.locator(".memory-summary")).not_to_contain_text("回答先给结论")
    switch = dialog.get_by_role("switch", name="仅使用本项目记忆", exact=True)
    switch.click()
    page.get_by_role("alertdialog").get_by_role("button", name="仅用本项目", exact=True).click()
    expect(switch).to_have_attribute("aria-checked", "true")
    assert httpx.get(stack.api + f"/v1/projects/{project['id']}", headers=headers).json()["memory_mode"] == "isolated"
    switch.click()
    page.get_by_role("alertdialog").get_by_role("button", name="互通", exact=True).click()
    expect(switch).to_have_attribute("aria-checked", "false")
    # the merge runs in the background; the mock judges every project fact general, so it moves to the account
    expect(dialog.locator(".memory-summary-empty")).to_have_text("还没有记忆", timeout=30000)
    entries = httpx.get(stack.api + "/v1/memory", headers=headers).json()["entries"]
    assert {e["content"]: e["project_id"] for e in entries} == {"回答先给结论": None, "季报口径以财务部为准": None}
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.screenshot(path=str(stack.log_dir / f"project-memory-{width}.png"), full_page=True, animations="disabled")
    dialog.get_by_role("button", name="关闭", exact=True).click()
    reset_memory(stack, headers)
    assert httpx.delete(stack.api + f"/v1/projects/{project['id']}", headers=headers).status_code == 204


def test_conversation_menu_controls_memory_read_and_write(page, stack):
    headers = auth(stack)
    conv = httpx.post(stack.api + "/v1/conversations", headers=headers, json={"title": "记忆策略验收"}).json()
    login(page, stack, path=f"/c/{conv['id']}")
    page.get_by_role("button", name="更多操作 记忆策略验收", exact=True).first.click()
    read = page.get_by_role("menuitemcheckbox", name="使用已有记忆")
    write = page.get_by_role("menuitemcheckbox", name="允许记住本对话")
    read.click()
    expect(read).to_have_attribute("aria-checked", "false")
    write.click()
    expect(write).to_have_attribute("aria-checked", "false")
    value = httpx.get(stack.api + f"/v1/conversations/{conv['id']}", headers=headers).json()
    assert value["memory_policy"] == {"read": False, "write": False}


def test_conversation_menu_stays_put_and_hangs_from_its_button(page, stack):
    headers = auth(stack)
    conv = httpx.post(stack.api + "/v1/conversations", headers=headers, json={"title": "菜单位置验收"}).json()
    login(page, stack, path=f"/c/{conv['id']}")
    button = page.locator(".conv-item .conv-more").first
    page.locator(".conv-item").first.hover()
    before = button.bounding_box()
    # pressing resets a button's transform; the trigger must not be positioned by one
    button.dispatch_event("pointerdown")
    page.mouse.move(before["x"] + before["width"] / 2, before["y"] + before["height"] / 2)
    page.mouse.down()
    pressed = button.bounding_box()
    page.mouse.up()
    assert abs(pressed["y"] - before["y"]) < 1, (before, pressed)
    menu = page.get_by_role("menu")
    expect(menu).to_be_visible()
    menu.evaluate("(m) => Promise.all(m.getAnimations().map((a) => a.finished))")
    box = menu.bounding_box()
    assert abs(box["x"] + box["width"] - (before["x"] + before["width"])) < 1, (box, before)
    assert 0 <= box["y"] - (before["y"] + before["height"]) <= 6, (box, before)
    assert menu.evaluate("(m) => getComputedStyle(m).transformOrigin.startsWith(String(m.offsetWidth))")
    page.screenshot(path=str(stack.log_dir / "conversation-menu.png"), clip={"x": 0, "y": 340, "width": 300, "height": 380})
    assert httpx.delete(stack.api + f"/v1/conversations/{conv['id']}", headers=headers).status_code == 204
