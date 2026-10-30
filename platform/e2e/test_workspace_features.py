"""Workspace contracts through all five services and the production browser UI."""

from __future__ import annotations

import re
import time
import uuid
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from .conftest import PASSWORD, wait_models
from .test_integration_features import _menu
from .test_portal import _send, _start_conversation, _wait_for_turns

pytestmark = pytest.mark.e2e
EVIDENCE = Path("/tmp/ailab-workspace-evidence/screenshots")


@pytest.fixture(autouse=True)
def workspace_viewport(page):
    page.set_viewport_size({"width": 1360, "height": 860})


def shot(page, name, mobile=False):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    size = {"width": 390 if mobile else 1360, "height": 844 if mobile else 860}
    if page.viewport_size != size:
        page.set_viewport_size(size)
    if mobile and not page.locator('[role="dialog"], [role="menu"]').count() and page.locator(".shell:not(.collapsed)").count():
        page.get_by_role("button", name="收起侧栏", exact=True).click()
    page.screenshot(path=str(EVIDENCE / f"{name}-{'phone' if mobile else 'desktop'}.png"), full_page=True, animations="disabled")
    if mobile:
        assert page.evaluate("document.documentElement.scrollWidth === innerWidth")


def api(page, stack, method, path, data=None):
    response = getattr(page.request, method)(stack.portal + "/api/v1" + path, **({"data": data} if data is not None else {}))
    assert response.ok, response.text()
    return response.json() if response.status != 204 else None


def wait_api(page, stack, path, predicate, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = api(page, stack, "get", path)
        if predicate(result):
            return result
        page.wait_for_timeout(150)
    raise AssertionError(f"API condition not met: {path}; last={result}")


def project(page, stack):
    created = api(page, stack, "post", "/projects", {"name": "验收项目-" + uuid.uuid4().hex[:8]})
    page.reload()
    wait_models(page)
    return created


def release(stack):
    httpx.post(stack.agent + "/e2e/workspace-release", timeout=5).raise_for_status()


def hold(page, stack, failure=False):
    page.get_by_role("radio", name="工作", exact=True).click()
    prompt = ("工作区失败" if failure else "工作区保持") + "-" + uuid.uuid4().hex[:8]
    conv = _start_conversation(page, prompt)
    page.wait_for_function("document.querySelector('.turn') && !document.querySelector('.turn-meta')")
    # Confirm the model is actually holding, rather than racing bootstrap.
    end = time.monotonic() + 10
    while time.monotonic() < end:
        if prompt in httpx.get(stack.agent + "/e2e/workspace-seen").json():
            return conv, prompt
        page.wait_for_timeout(100)
    raise AssertionError("scripted model did not start")


def test_project_brief_persists_and_reaches_model(logged_in, stack):
    page = logged_in
    p = project(page, stack)
    brief = "面向初中生，用温和简短的中文解释。"
    try:
        page.goto(stack.portal + "/p/" + p["id"])
        page.get_by_role("button", name="项目说明", exact=True).click()
        page.get_by_role("textbox", name="项目说明", exact=True).fill(brief)
        page.locator(".project-brief").get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_role("button", name="项目说明", exact=True)).to_contain_text(brief)
        page.reload()
        expect(page.get_by_role("button", name="项目说明", exact=True)).to_contain_text(brief)
        shot(page, "project-brief")
        shot(page, "project-brief", True)
        page.set_viewport_size({"width": 1360, "height": 860})
        page.get_by_role("radio", name="对话", exact=True).click()
        prompt = "工作区说明-" + uuid.uuid4().hex[:8]
        conv = _start_conversation(page, prompt)
        _wait_for_turns(page, 1)
        messages = httpx.get(stack.agent + "/e2e/workspace-seen").json()[prompt]
        assert brief in messages[0]["content"]
        assert api(page, stack, "get", "/conversations/" + conv)["project_id"] == p["id"]
    finally:
        api(page, stack, "delete", "/projects/" + p["id"])


@pytest.mark.parametrize("mobile", [False, True])
def test_sidebar_project_menu_renames_edits_brief_and_deletes_its_open_conversation(logged_in, stack, mobile):
    page = logged_in
    p = project(page, stack)
    if mobile:
        page.set_viewport_size({"width": 390, "height": 844})
    if page.locator(".shell.collapsed").count():
        page.get_by_role("button", name="展开侧栏", exact=True).click()
    row = page.locator(".project-item").filter(has=page.locator(f'a[href="/p/{p["id"]}"]'))
    try:
        row.get_by_role("button", name="更多操作 " + p["name"], exact=True).click()
        menu = page.get_by_role("menu")
        assert menu.evaluate("el => el.parentElement === document.body")
        menu.get_by_role("menuitem", name="重命名", exact=True).click()
        field = page.get_by_role("textbox", name="项目名称", exact=True)
        field.fill(p["name"] + "-改名")
        field.press("Enter")
        expect(row).to_contain_text(p["name"] + "-改名")
        row.get_by_role("button").click()
        page.get_by_role("menuitem", name="项目说明", exact=True).click()
        brief = page.get_by_role("textbox", name="项目说明", exact=True)
        brief.fill("菜单打开的项目说明")
        page.locator(".project-brief").get_by_role("button", name="保存", exact=True).click()
        expect(page.get_by_role("button", name="项目说明", exact=True)).to_contain_text("菜单打开的项目说明")
        conv = api(page, stack, "post", "/conversations", {"project_id": p["id"]})
        page.goto(stack.portal + "/c/" + conv["id"])
        wait_models(page)
        if page.locator(".shell.collapsed").count():
            page.get_by_role("button", name="展开侧栏", exact=True).click()
        row.get_by_role("button").click()
        page.get_by_role("menuitem", name="删除项目", exact=True).click()
        page.get_by_role("alertdialog").get_by_role("button", name="删除", exact=True).click()
        page.wait_for_url(stack.portal + "/")
        expect(row).to_have_count(0)
        assert page.request.get(stack.portal + "/api/v1/conversations/" + conv["id"]).status == 404
        p = None
    finally:
        if p:
            api(page, stack, "delete", "/projects/" + p["id"])


def test_queue_edit_remove_auto_dispatch_and_mobile(logged_in, stack):
    page = logged_in
    conv, _ = hold(page, stack)
    try:
        _send(page, "工作区续发-first")
        row = page.locator(".queue-row").filter(has_text="工作区续发-first")
        expect(row).to_be_visible()
        row.get_by_role("button", name="修改", exact=True).click()
        field = page.get_by_role("textbox", name="修改待发送的消息", exact=True)
        field.fill("工作区续发-edited")
        field.press("Enter")
        expect(page.locator(".queue-text")).to_have_text("工作区续发-edited")
        _send(page, "remove-me")
        page.locator(".queue-row").filter(has_text="remove-me").get_by_role("button", name="移除", exact=True).click()
        expect(page.locator(".queue-row")).to_have_count(1)
        shot(page, "queue")
        shot(page, "queue", True)
        release(stack)
        _wait_for_turns(page, 2)
        expect(page.locator(".msg.user .bubble").last).to_have_text("工作区续发-edited")
        expect(page.locator(".queue-row")).to_have_count(0)
        turns = api(page, stack, "get", "/conversations/" + conv)["turns"]
        assert len(turns) == 2 and turns[1]["parent_run_id"] == turns[0]["id"]
    finally:
        release(stack)


def test_queue_mutation_survives_an_older_detail_response(logged_in, stack):
    page = logged_in
    held = []
    snapshots = []
    hold_reads = True

    def detail(route):
        response = route.fetch()
        snapshot = response.json()
        snapshots.append(snapshot)
        if not hold_reads:
            route.fulfill(response=response)
        else:
            held.append((route, snapshot))

    page.route("**/api/v1/conversations/*?limit=20", detail)
    conv, _ = hold(page, stack)
    try:
        deadline = time.monotonic() + 10
        while not held and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        assert held, "initial conversation detail was not requested"
        assert all(not snapshot["queue"] for _, snapshot in held)
        _send(page, "工作区续发-keep-after-refresh")
        row = page.locator(".queue-row").filter(has_text="工作区续发-keep-after-refresh")
        expect(row).to_be_visible()
        # Complete the latest read only after the confirmed write is already on screen.
        hold_reads = False
        for route, snapshot in held:
            route.fulfill(json=snapshot)
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        expect(row).to_be_visible()
        assert api(page, stack, "get", "/conversations/" + conv)["queue"][0]["prompt"] == "工作区续发-keep-after-refresh"
    finally:
        page.unroute("**/api/v1/conversations/*?limit=20", detail)
        release(stack)


def test_steer_confirmed_event_bubble_and_reload(logged_in, stack):
    page = logged_in
    conv, _ = hold(page, stack)
    try:
        field = page.get_by_role("textbox", name="消息", exact=True)
        field.fill("请增加一个简短例子")
        field.press("Alt+Enter")
        expect(page.locator(".queue-row.steer")).to_contain_text("请增加一个简短例子")
        release(stack)
        _wait_for_turns(page, 1)
        expect(page.locator(".bubble.steer")).to_contain_text("请增加一个简短例子")
        data = api(page, stack, "get", "/conversations/" + conv)
        assert data["turns"][0]["steered"][0]["prompt"] == "请增加一个简短例子"
        shot(page, "steer")
        page.reload()
        expect(page.locator(".bubble.steer")).to_contain_text("请增加一个简短例子")
        shot(page, "steer", True)
    finally:
        release(stack)


def test_queue_failure_pauses_then_continue(logged_in, stack):
    page = logged_in
    conv, _ = hold(page, stack, failure=True)
    try:
        _send(page, "工作区续发-after-failure")
        expect(page.locator(".queue-row")).to_have_count(1)
        release(stack)
        expect(page.get_by_role("button", name="继续发送", exact=True)).to_be_visible(timeout=15000)
        assert api(page, stack, "get", "/conversations/" + conv)["queue_paused"]
        shot(page, "queue-paused")
        shot(page, "queue-paused", True)
        page.set_viewport_size({"width": 1360, "height": 860})
        page.get_by_role("button", name="继续发送", exact=True).click()
        expect(page.locator(".msg.user .bubble").last).to_have_text("工作区续发-after-failure", timeout=15000)
        expect(page.locator(".assistant-body > .md").last).to_contain_text("工作区完成", timeout=15000)
    finally:
        release(stack)


def test_inbox_schedule_badge_click_and_read_all(logged_in, stack):
    page = logged_in
    api(page, stack, "post", "/notifications/read", {"all": True})
    body = {
        "title": "通知验收-" + uuid.uuid4().hex[:8],
        "prompt": "工作区说明通知",
        "mode": "chat",
        "timezone": "UTC",
        "schedule": {"kind": "daily", "time": "09:00"},
    }
    task = api(page, stack, "post", "/schedules", body)
    try:
        started = api(page, stack, "post", "/schedules/" + task["id"] + "/run")
        wait_api(page, stack, "/notifications/unread", lambda d: d["unread"] > 0)
        page.reload()
        bell = page.get_by_role("button", name=re.compile(r"^通知（"))
        expect(bell).to_be_visible()
        bell.click()
        dialog = page.get_by_role("dialog", name="通知", exact=True)
        expect(dialog).to_contain_text(task["title"])
        shot(page, "inbox")
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name=re.compile(r"^通知（")).click()
        shot(page, "inbox", True)
        dialog.locator(".inbox-row").filter(has_text=task["title"]).first.click()
        page.wait_for_url("**/c/" + started["conversation_id"])
        wait_api(page, stack, "/notifications/unread", lambda d: d["unread"] == 0)
        api(page, stack, "post", "/schedules/" + task["id"] + "/run")
        wait_api(page, stack, "/notifications/unread", lambda d: d["unread"] > 0)
        page.reload()
        page.get_by_role("button", name=re.compile(r"^通知（")).click()
        page.get_by_role("button", name="全部已读", exact=True).click()
        wait_api(page, stack, "/notifications/unread", lambda d: d["unread"] == 0)
    finally:
        api(page, stack, "delete", "/schedules/" + task["id"])


def register(context, stack, name):
    p = context.new_page()
    p.goto(stack.portal + "/register")
    p.fill("input[name=username]", name)
    p.fill("input[name=display_name]", name)
    p.fill("input[name=password]", PASSWORD)
    p.fill("input[name=again]", PASSWORD)
    p.click("button[type=submit]")
    p.wait_for_selector(".user-chip", timeout=15000)
    return p


def test_people_share_artifacts_revoke_and_move_conversation(logged_in, stack, browser):
    page = logged_in
    second = browser.new_context(viewport={"width": 1360, "height": 860})
    third = browser.new_context()
    bname = "shareb" + uuid.uuid4().hex[:8]
    b = register(second, stack, bname)
    c = register(third, stack, "sharec" + uuid.uuid4().hex[:8])
    p = project(page, stack)
    try:
        page.get_by_role("radio", name="工作", exact=True).click()
        conv = _start_conversation(page, "生成文件用于分享验收")
        _wait_for_turns(page, 1)
        data = api(page, stack, "get", "/conversations/" + conv)
        artifact = data["turns"][0]["artifacts"][0]
        _menu(page, conv)
        page.get_by_role("menuitem", name="分享", exact=True).click()
        dialog = page.locator(".share-dialog")
        dialog.get_by_role("textbox", name="添加同事").fill(bname)
        dialog.get_by_role("option").filter(has_text=bname).click()
        dialog.get_by_role("button", name="创建链接", exact=True).click()
        link = dialog.get_by_role("textbox", name="分享链接")
        expect(link).to_have_value(re.compile(r"/s/shr_"))
        url = link.input_value()
        sid = url.rsplit("/", 1)[-1]
        shot(page, "share-dialog")
        shot(page, "share-dialog", True)
        page.set_viewport_size({"width": 1360, "height": 860})
        b.reload()
        b.get_by_role("button", name=re.compile(r"^通知（")).click()
        inbox = b.get_by_role("dialog", name="通知", exact=True)
        expect(inbox).to_contain_text("分享")
        inbox.locator(".inbox-row").first.click()
        b.wait_for_url("**/s/" + sid)
        expect(b.locator(".msg.user .bubble")).to_contain_text("生成文件用于分享验收")
        expect(b.locator(".assistant-body > .md")).not_to_be_empty()
        download = api(b, stack, "get", "/shares/" + sid + "/view")["turns"][0]["artifacts"][0]["download_url"]
        response = b.request.get(stack.portal + (download if download.startswith("/api/") else "/api" + download))
        assert response.ok and response.body()
        b.get_by_role("button", name="预览", exact=True).first.click()
        expect(b.locator(".viewer")).to_be_visible()
        shot(b, "share-preview")
        shot(b, "share-preview", True)
        b.set_viewport_size({"width": 1360, "height": 860})
        b.keyboard.press("Escape")
        shot(b, "share-page")
        shot(b, "share-page", True)
        c.goto(url)
        expect(c.get_by_text("这个分享不存在或已停止", exact=False)).to_be_visible()
        # Move the conversation and its artifact, retaining share authorization.
        page.keyboard.press("Escape")
        page.get_by_role("button", name="取消", exact=True).click() if page.locator(".share-dialog").count() else None
        _menu(page, conv)
        page.get_by_role("menuitem", name="移到项目", exact=True).click()
        expect(page.get_by_role("checkbox", name="连同产出的文件", exact=True)).to_be_checked()
        page.get_by_role("button", name=p["name"], exact=True).click()
        wait_api(page, stack, "/files/" + artifact["file_id"], lambda f: f["project_id"] == p["id"])
        assert api(b, stack, "get", "/shares/" + sid + "/view")["turns"]
        page.goto(stack.portal + "/p/" + p["id"])
        page.get_by_role("tab", name=re.compile("^文件")).click()
        moved = api(page, stack, "get", "/files/" + artifact["file_id"])
        expect(page.get_by_role("button", name="预览 " + moved["filename"], exact=True)).to_be_visible()
        shot(page, "project-moved-files")
        shot(page, "project-moved-files", True)
        page.set_viewport_size({"width": 1360, "height": 860})
        api(page, stack, "delete", "/shares/" + sid)
        b.reload()
        expect(b.get_by_text("这个分享不存在或已停止", exact=False)).to_be_visible()
    finally:
        api(page, stack, "delete", "/projects/" + p["id"])
        second.close()
        third.close()


def test_library_summary_file_menu_and_switch(logged_in, stack, tmp_path):
    page = logged_in
    p = project(page, stack)
    path = tmp_path / ("summary-" + uuid.uuid4().hex[:8] + ".txt")
    path.write_text("城市节水方案介绍雨水收集、漏水检测与居民参与，每月记录用量并评估效果。" * 30)
    try:
        api(page, stack, "patch", "/admin/settings/library", {"summaries": True})
        page.goto(stack.portal + "/files")
        page.set_input_files("input[type=file]", str(path))
        row = page.locator(".lib-row").filter(has_text=path.name)
        row.get_by_role("link", name="下载 " + path.name, exact=True).wait_for(timeout=30000)
        files = wait_api(
            page,
            stack,
            "/files",
            lambda fs: any(f["filename"] == path.name and f.get("meta", {}).get("summary", {}).get("status") == "done" for f in fs),
        )
        file = next(f for f in files if f["filename"] == path.name)
        # Preserve every word; the mock includes bold and a quoted last paragraph.
        summary = file["meta"]["summary"]["text"].replace("**", "")
        summary = " ".join(re.sub(r"(?m)^>\s*", "", summary).split())
        expect(row.locator(".lib-summary")).to_have_text(summary, timeout=15000)
        expect(row.locator(".lib-summary")).not_to_contain_text("**")
        shot(page, "library-summary")
        shot(page, "library-summary", True)
        page.set_viewport_size({"width": 1360, "height": 860})
        row.get_by_role("button", name="更多操作 " + path.name, exact=True).click()
        page.get_by_role("menuitem", name="分享", exact=True).click()
        page.get_by_role("radio", name="所有同事", exact=True).click()
        page.get_by_role("button", name="创建链接", exact=True).click()
        expect(page.get_by_role("textbox", name="分享链接")).to_have_value(re.compile("/s/"))
        page.keyboard.press("Escape")
        row.get_by_role("button", name="更多操作 " + path.name, exact=True).click()
        page.get_by_role("menuitem", name="移动到项目", exact=True).click()
        page.get_by_role("button", name=p["name"], exact=True).click()
        wait_api(page, stack, "/files/" + file["id"], lambda f: f["project_id"] == p["id"])
        page.goto(stack.portal + "/p/" + p["id"])
        page.get_by_role("tab", name=re.compile("^文件")).click()
        page.get_by_role("button", name="预览 " + path.name, exact=True).click()
        expect(page.locator(".viewer")).to_be_visible()
        expect(page.get_by_role("button", name="分享 " + path.name, exact=True)).to_be_visible()
        page.get_by_role("link", name="围绕 " + path.name + " 对话", exact=True).click()
        page.wait_for_url("**/p/" + p["id"] + "?files=" + file["id"])
        expect(page.locator(".attach-chip").filter(has_text=path.name)).to_be_visible()
        shot(page, "file-question")
        shot(page, "file-question", True)
        page.set_viewport_size({"width": 1360, "height": 860})
        page.goto(stack.portal + "/admin")
        page.get_by_role("tab", name="上下文", exact=True).click()
        switch = page.get_by_role("switch", name="为资料库文件生成摘要", exact=True)
        expect(switch).to_have_attribute("aria-checked", "true")
        switch.click()
        wait_api(page, stack, "/admin/settings/library", lambda d: not d["summaries"])
        page.reload()
        page.get_by_role("tab", name="上下文", exact=True).click()
        expect(switch).to_have_attribute("aria-checked", "false")
        shot(page, "library-summary-switch")
        shot(page, "library-summary-switch", True)
        page.set_viewport_size({"width": 1360, "height": 860})
    finally:
        api(page, stack, "patch", "/admin/settings/library", {"summaries": True})
        api(page, stack, "delete", "/projects/" + p["id"])


def test_brand_upload_analysis_and_settings_persist(logged_in, stack, tmp_path):
    from PIL import Image

    page = logged_in
    old = api(page, stack, "get", "/admin/brand")
    from docx import Document

    template = tmp_path / "brand-template.docx"
    doc = Document()
    doc.add_heading("Brand template", 0)
    doc.save(template)
    logo = tmp_path / "logo.png"
    Image.new("RGB", (24, 24), "#123456").save(logo)
    try:
        page.goto(stack.portal + "/admin")
        page.get_by_role("tab", name="品牌", exact=True).click()
        page.locator(".brand-slot").filter(has=page.get_by_role("button", name="上传Word 模板", exact=True)).locator(
            "input[type=file]"
        ).set_input_files(str(template))
        page.locator(".brand-slot").filter(has=page.get_by_role("button", name="上传Logo", exact=True)).locator(
            "input[type=file]"
        ).set_input_files(str(logo))
        expect(page.get_by_text(re.compile(r"\d+ 种样式"))).to_be_visible(timeout=30000)
        expect(page.get_by_role("button", name="替换Logo", exact=True)).to_be_visible()
        switch = page.get_by_role("switch", name="生成文件使用公司品牌", exact=True)
        if switch.get_attribute("aria-checked") == "false":
            switch.click()
        page.get_by_label("公司名称", exact=True).fill("验收品牌")
        page.get_by_role("textbox", name="主色色值", exact=True).fill("#123456")
        page.get_by_role("textbox", name="强调色色值", exact=True).fill("#ABCDEF")
        page.locator(".brand-settings").get_by_role("button", name="保存", exact=True).click()
        wait_api(page, stack, "/admin/brand", lambda d: d["enabled"] and d["colors"]["primary"] == "#123456")
        page.reload()
        page.get_by_role("tab", name="品牌", exact=True).click()
        expect(switch).to_have_attribute("aria-checked", "true")
        expect(page.get_by_role("textbox", name="主色色值", exact=True)).to_have_value("#123456")
        shot(page, "brand-admin")
        shot(page, "brand-admin", True)
    finally:
        for kind in ("logo", "docx"):
            api(page, stack, "delete", "/admin/brand/assets/" + kind)
        api(page, stack, "patch", "/admin/brand", {k: old[k] for k in ("enabled", "name", "colors", "font")})


def test_artifact_menu_moves_file_and_questions_in_its_project(logged_in, stack):
    page = logged_in
    p = project(page, stack)
    try:
        page.get_by_role("radio", name="工作", exact=True).click()
        conv = _start_conversation(page, "生成文件验证产物菜单")
        _wait_for_turns(page, 1)
        file = api(page, stack, "get", "/conversations/" + conv)["turns"][0]["artifacts"][0]
        button = page.get_by_role("button", name="更多操作 " + file["filename"], exact=True)
        button.click()
        expect(page.get_by_role("menuitem", name="提问", exact=True)).to_be_visible()
        expect(page.get_by_role("menuitem", name="分享", exact=True)).to_be_visible()
        shot(page, "artifact-menu")
        page.set_viewport_size({"width": 390, "height": 844})
        if page.locator(".shell:not(.collapsed)").count():
            page.get_by_role("button", name="收起侧栏", exact=True).click()
        button.click()
        shot(page, "artifact-menu", True)
        page.get_by_role("menuitem", name="移到项目", exact=True).click()
        page.get_by_role("button", name=p["name"], exact=True).click()
        wait_api(page, stack, "/files/" + file["file_id"], lambda f: f["project_id"] == p["id"])
        button.click()
        page.get_by_role("menuitem", name="提问", exact=True).click()
        page.wait_for_url("**/p/" + p["id"] + "?files=" + file["file_id"])
        expect(page.locator(".attach-chip").filter(has_text=file["filename"])).to_be_visible()
    finally:
        api(page, stack, "delete", "/projects/" + p["id"])
