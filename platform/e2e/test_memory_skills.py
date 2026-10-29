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
            "project_only": False,
            "entries": [],
        },
    )
    assert result.status_code == 200


def test_memory_edit_reload_conflict_and_controls(page, stack):
    headers = auth(stack)
    reset_memory(stack, headers)
    login(page, stack, path="/memory")
    page.get_by_role("button", name="新建", exact=True).first.click()
    page.get_by_role("textbox", name="个人偏好", exact=True).fill("常用简洁中文")
    page.get_by_role("button", name="保存", exact=True).click()
    expect(page.get_by_role("button", name=re.compile(r"^保存(?:中…)?$"))).to_have_count(0)
    page.reload()
    expect(page.get_by_role("textbox", name="个人偏好", exact=True)).to_have_value("常用简洁中文")
    page.get_by_role("textbox", name="个人偏好", exact=True).fill("本页未保存草稿")
    current = httpx.get(stack.api + "/v1/memory", headers=headers).json()
    update = {
        "expected_revision": current["revision"],
        "enabled": True,
        "auto_save": True,
        "entries": [{"id": current["entries"][0]["id"], "kind": "user", "content": "另一个标签页的新版本"}],
    }
    assert httpx.put(stack.api + "/v1/memory", headers=headers, json=update).status_code == 200
    page.get_by_role("button", name="保存", exact=True).click()
    expect(page.get_by_role("button", name="重新加载", exact=True)).to_be_visible()
    expect(page.get_by_role("textbox", name="个人偏好", exact=True)).to_have_value("本页未保存草稿")
    page.get_by_role("button", name="重新加载", exact=True).click()
    expect(page.get_by_role("textbox", name="个人偏好", exact=True)).to_have_value("本页未保存草稿")
    page.get_by_role("button", name="保存", exact=True).click()
    expect(page.get_by_role("button", name=re.compile(r"^保存(?:中…)?$"))).to_have_count(0)
    page.get_by_role("switch", name="自动记忆", exact=True).click()
    expect(page.get_by_role("switch", name="自动记忆", exact=True)).to_have_attribute("aria-checked", "false")
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
    expect(page.get_by_role("textbox", name="个人偏好", exact=True)).to_have_value("验收偏好：回答保持简洁")
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
    row = page.locator(".skill-row").filter(has_text=name)
    row.get_by_role("button", name="编辑 浏览器验收技能", exact=True).wait_for(timeout=60000)
    expect(row.get_by_role("button", name="编辑 浏览器验收技能", exact=True)).to_be_enabled(timeout=60000)
    row.get_by_role("button", name="编辑 浏览器验收技能", exact=True).click()
    editor.get_by_role("textbox").fill(content + "第二步校验结果。\n")
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/v1/skills/content")) as saved:
        editor.get_by_role("button", name="保存", exact=True).click()
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
    expect(editor.get_by_role("textbox")).to_have_value(content + "第二步校验结果。\n")
    page.screenshot(path=str(stack.log_dir / "skill-edit.png"), animations="disabled")
    editor.get_by_role("button", name="取消", exact=True).click()
    headers = auth(stack)
    skill = next(s for s in httpx.get(stack.api + "/v1/skills", headers=headers).json() if s["name"] == name)
    assert len(httpx.get(stack.api + f"/v1/files/{skill['file_id']}/versions", headers=headers).json()) == 2
    assert httpx.delete(stack.api + f"/v1/skills/files/{skill['file_id']}", headers=headers).status_code == 204


@pytest.mark.parametrize("width", [1280, 390])
def test_memory_scope_sources_review_pause_and_history_controls(page, stack, width):
    headers = auth(stack)
    reset_memory(stack, headers)
    project = httpx.post(stack.api + "/v1/projects", headers=headers, json={"name": "记忆验收" + uuid.uuid4().hex[:6]}).json()
    conv = httpx.post(stack.api + "/v1/conversations", headers=headers, json={"title": "来源验收"}).json()
    run = httpx.post(
        stack.api + f"/v1/conversations/{conv['id']}/messages", headers=headers, json={"prompt": "我喜欢蓝色主题", "mode": "chat"}
    ).json()
    current = httpx.get(stack.api + "/v1/memory", headers=headers).json()
    saved = httpx.put(
        stack.api + "/v1/memory",
        headers=headers,
        json={
            "expected_revision": current["revision"],
            "entries": [
                {"kind": "user", "content": "我喜欢蓝色主题", "source_quote": "我喜欢蓝色主题", "evidence_run_ids": [run["id"]]},
                {"content": "需要确认的敏感事实", "status": "review", "project_id": project["id"]},
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    page.set_viewport_size({"width": width, "height": 900})
    login(page, stack, path="/memory")
    if width == 390 and page.get_by_role("button", name="收起侧栏", exact=True).is_visible():
        page.get_by_role("button", name="收起侧栏", exact=True).click()
    page.get_by_role("button", name="查看来源", exact=True).click()
    expect(page.locator(".memory-source blockquote")).to_have_text("我喜欢蓝色主题")
    expect(page.locator(".memory-source a")).to_have_attribute("href", f"/c/{conv['id']}")
    page.get_by_role("combobox", name="记忆范围", exact=True).select_option(project["id"])
    expect(page.get_by_role("textbox", name="个人偏好", exact=True)).to_have_count(0)
    page.get_by_role("button", name="确认使用", exact=True).click()
    page.get_by_role("button", name="保存", exact=True).click()
    expect(page.get_by_role("button", name=re.compile(r"^保存(?:中…)?$"))).to_have_count(0)
    page.get_by_role("button", name="暂停", exact=True).click()
    page.get_by_role("button", name="保存", exact=True).click()
    expect(page.get_by_role("button", name=re.compile(r"^保存(?:中…)?$"))).to_have_count(0)
    page.reload()
    expect(page.get_by_role("button", name="恢复", exact=True)).to_be_visible()
    page.get_by_role("switch", name="引用历史对话", exact=True).click()
    expect(page.get_by_role("switch", name="引用历史对话", exact=True)).to_have_attribute("aria-checked", "false")
    assert httpx.get(stack.api + "/v1/memory", headers=headers).json()["reference_history"] is False
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert page.get_by_role("textbox", name="个人偏好", exact=True).evaluate("el => el.getBoundingClientRect().width") > 250
    page.screenshot(path=str(stack.log_dir / f"memory-governance-{width}.png"), full_page=True, animations="disabled")
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
