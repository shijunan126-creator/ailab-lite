"""User-controlled previews and chronological history against the temporary real stack."""

from __future__ import annotations

import json
import subprocess
import sys

import httpx
import pytest

from .conftest import PASSWORD, PLATFORM, SERVICE_TOKENS, USERNAME, confirm_action, login

pytestmark = pytest.mark.e2e
_seeded = []
_attempts = {}


@pytest.fixture(autouse=True)
def cleanup_seeded(stack):
    yield
    token = httpx.post(stack.api + "/v1/auth/login", json={"username": USERNAME, "password": PASSWORD}).json()["token"]
    for run_id in _seeded:
        response = httpx.post(stack.api + f"/v1/runs/{run_id}/cancel", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code in (200, 409), response.text
    _seeded.clear()
    _attempts.clear()


def seed(stack, count=1, active=True, title="预览与历史验收"):
    code = (
        """
import json
from datetime import timedelta
from uuid import uuid4
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, Run, RunAttempt, User, utcnow
from ailab_platform.api.jobs import stage_job
from ailab_platform.storage import get_storage
with SessionLocal() as db:
    user = db.query(User).filter_by(username="e2e-user").one()
    conv = Conversation(user_id=user.id, title=TITLE)
    db.add(conv); db.flush()
    runs=[]
    for i in range(COUNT):
        run=Run(user_id=user.id, conversation_id=conv.id, parent_run_id=runs[-1] if runs else None,
            prompt=f"历史消息 {i:03}", model="test-fast", status="succeeded",
            summary=(f"第 {i} 轮分析。"+"这是一段用于保持阅读位置的内容。"*30), created_at=utcnow()-timedelta(days=30)+timedelta(seconds=i))
        db.add(run); db.flush(); runs.append(run.id)
    conv.active_run_id = run.id
    attempt = None
    if ACTIVE:
        run.status="running"
        attempt = uuid4().hex
        job = stage_job(db, "run", run_id=run.id)
        job.claim_token = attempt
        job.claimed_until = job.next_attempt_at = utcnow()+timedelta(hours=1)
        db.add(RunAttempt(id=attempt, run_id=run.id, number=1, agent_instance="browser-fixture"))
    db.commit()
    sheet={"sheets":[{"name":"销售", "rows":[[{"v":"地区"},{"v":"金额"}],[{"v":"华东"},{"v":"120"}]],
        "widths":[12,12], "size":[2,2],"charts":0,"truncated":False}]}
    get_storage().put_bytes(f"previews/{run.id}/1/sheets.json", json.dumps(sheet).encode())
    print(json.dumps({"id":conv.id,"run":run.id,"attempt":attempt}))
""".replace("COUNT", str(count))
        .replace("ACTIVE", repr(active))
        .replace("TITLE", repr(title))
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, capture_output=True, text=True, check=True)
    seeded = json.loads(result.stdout)
    _seeded.append(seeded["run"])
    _attempts[seeded["run"]] = seeded["attempt"]
    return seeded


def emit(stack, run, filename, version):
    r = httpx.post(
        stack.api + f"/internal/runs/{run}/events",
        headers={"X-Service-Token": SERVICE_TOKENS["agent"], "X-Run-Attempt": _attempts[run]},
        json={"type": "preview.updated", "payload": {"filename": filename, "kind": "sheets", "data": "1/sheets.json", "version": version}},
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("mobile", [False, True])
def test_preview_updates_never_open_or_steal_selection_and_back_closes(page, stack, mobile):
    if mobile:
        page.set_viewport_size({"width": 390, "height": 844})
    conv = seed(stack)
    login(page, stack, path="/c/" + conv["id"])
    page.wait_for_selector(".turn")
    emit(stack, conv["run"], "first.xlsx", 1)
    page.get_by_role("button", name="打开预览（1）").wait_for()
    assert page.locator(".preview-pane").count() == 0
    page.get_by_role("button", name="打开预览（1）").click()
    page.wait_for_selector(".sheet-grid td")
    assert page.locator(".pane-tab.on .pane-tab-name").evaluate("(el)=>el===document.activeElement")
    emit(stack, conv["run"], "second.xlsx", 1)
    page.wait_for_selector('.pane-tab:has-text("second.xlsx")')
    assert page.locator(".pane-tab.on").inner_text() == "first.xlsx"
    page.screenshot(path=str(stack.log_dir / ("preview-open-mobile.png" if mobile else "preview-open-desktop.png")))
    assert page.locator(".preview-pane").bounding_box()["width"] <= page.viewport_size["width"]
    if mobile:
        page.keyboard.press("Tab")
        assert page.evaluate("!!document.activeElement.closest('.preview-pane')")
    emit(stack, conv["run"], "first.xlsx", 2)
    page.keyboard.press("Escape")
    page.wait_for_selector(".preview-pane", state="detached")
    emit(stack, conv["run"], "third.xlsx", 1)
    page.get_by_role("button", name="打开预览（3）").wait_for()
    assert page.locator(".preview-pane").count() == 0
    page.get_by_role("button", name="打开预览（3）").click()
    assert page.locator(".pane-tab.on").inner_text() == "first.xlsx"
    if mobile:
        page.go_back()
    else:
        page.keyboard.press("Escape")
    page.wait_for_selector(".preview-pane", state="detached")
    assert page.url.endswith("/c/" + conv["id"])
    page.screenshot(path=str(stack.log_dir / ("preview-mobile.png" if mobile else "preview-desktop.png")))


def test_older_page_keeps_the_visible_turn_and_refresh_keeps_history(page, stack):
    conv = seed(stack, count=45, active=False)
    login(page, stack, path="/c/" + conv["id"])
    page.wait_for_function("document.querySelectorAll('.turn').length===20")
    # Keep the first visible turn as an anchor while new content is prepended.
    page.locator(".messages").evaluate("(el)=>el.scrollTop=100")
    anchor = page.locator(".turn").first
    before = anchor.bounding_box()["y"]
    page.get_by_role("button", name="更早的消息").evaluate("(el)=>el.click()")
    page.wait_for_function("document.querySelectorAll('.turn').length===40")
    # The formerly first turn is now the 21st; its screen coordinate must be stable.
    after = page.locator(".turn").nth(20).bounding_box()["y"]
    assert abs(after - before) < 3, (before, after)
    page.locator(".messages").evaluate("(el)=>el.scrollTop=0")
    page.wait_for_function("document.querySelectorAll('.turn').length===45")
    assert page.get_by_role("button", name="更早的消息").count() == 0
    page.screenshot(path=str(stack.log_dir / "history-desktop.png"))


def test_switching_conversation_cancels_an_older_page_response(page, stack):
    old = seed(stack, count=25, active=False)
    new = seed(stack, count=1, active=False)
    login(page, stack, path="/c/" + old["id"])
    page.wait_for_function("document.querySelectorAll('.turn').length===20")
    pending = []

    def hold(route):
        pending.append(route)

    page.route("**/api/v1/conversations/" + old["id"] + "?**before=*", hold)
    page.get_by_role("button", name="更早的消息").evaluate("(el)=>el.click()")
    page.wait_for_timeout(100)
    assert pending
    page.locator(f'a[href="/c/{new["id"]}"]').first.click()
    page.wait_for_url("**/c/" + new["id"])
    page.wait_for_function("document.querySelectorAll('.turn').length===1")
    # A transport that delivers after cancellation must still be ignored by the component.
    pending[0].fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "id": old["id"],
                "turns": [],
                "previews": [],
                "has_more": False,
                "title": "旧响应不应显示",
            }
        ),
    )
    page.wait_for_timeout(100)
    assert page.locator(".turn").count() == 1
    assert page.get_by_text("旧响应不应显示").count() == 0


@pytest.mark.parametrize("mode", ["auto", "manual", "title_failed"])
def test_plain_text_answer_gets_model_title_without_overwriting_manual_name(page, stack, mode):
    conv = seed(stack, title="")
    login(page, stack, path="/c/" + conv["id"])
    page.wait_for_selector(".turn")
    service = {"X-Service-Token": SERVICE_TOKENS["agent"], "X-Run-Attempt": conv["attempt"]}
    internal = stack.api + f"/internal/runs/{conv['run']}"
    assert httpx.get(internal, headers=service).json()["needs_conversation_title"] is True
    if mode == "manual":
        token = httpx.post(stack.api + "/v1/auth/login", json={"username": USERNAME, "password": PASSWORD}).json()["token"]
        httpx.patch(
            stack.api + f"/v1/conversations/{conv['id']}", headers={"Authorization": f"Bearer {token}"}, json={"title": "我的手动名称"}
        ).raise_for_status()
    result = {} if mode == "title_failed" else {"conversation_title": "城市更新投资分析"}
    finished = httpx.post(
        internal + "/status",
        headers=service,
        json={"status": "succeeded", "summary": "这是普通文本的完整分析回答，没有 Markdown 标题。", "result": result},
    )
    assert finished.status_code == 200, finished.text
    page.wait_for_selector(".turn-meta")
    assert page.get_by_text("这是普通文本的完整分析回答，没有 Markdown 标题。", exact=True).is_visible()
    expected = "我的手动名称" if mode == "manual" else "城市更新投资分析" if mode == "auto" else "新对话"
    page.locator(f'a[href="/c/{conv["id"]}"]').filter(has_text=expected).wait_for()
    assert page.locator(".alert.error").count() == 0


def test_confirmed_delete_removes_sidebar_even_when_list_refresh_fails(page, stack):
    conv = seed(stack, active=False)
    login(page, stack, path="/c/" + conv["id"])
    row = page.locator(f".conv-item:has(a[href='/c/{conv['id']}'])")
    row.wait_for()
    page.route("**/api/v1/conversations", lambda route: route.abort())
    row.hover()
    row.get_by_role("button", name="更多操作 预览与历史验收").click()
    page.get_by_role("menuitem", name="删除", exact=True).click()
    confirm_action(page)
    page.wait_for_url(stack.portal + "/")
    row.wait_for(state="detached")


def test_failed_delete_keeps_conversation_and_reports_failure(page, stack):
    conv = seed(stack, active=False)
    login(page, stack, path="/c/" + conv["id"])
    row = page.locator(f".conv-item:has(a[href='/c/{conv['id']}'])")
    row.wait_for()
    page.route(f"**/api/v1/conversations/{conv['id']}", lambda route: route.fulfill(status=503, json={"detail": "unavailable"}))
    row.hover()
    row.get_by_role("button", name="更多操作 预览与历史验收").click()
    page.get_by_role("menuitem", name="删除", exact=True).click()
    confirm_action(page)
    page.get_by_role("alert").filter(has_text="删除失败，请重试").wait_for()
    assert page.url.endswith("/c/" + conv["id"])
    assert row.count() == 1
