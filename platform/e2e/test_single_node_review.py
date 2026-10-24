"""Regression acceptance for bounded polling, sparse search and branch races."""

import re
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from .test_conversation_ui import seed

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("mobile", [False, True])
def test_search_keeps_a_continuation_after_six_empty_pages(logged_in, stack, mobile):
    page = logged_in
    if mobile:
        page.set_viewport_size({"width": 390, "height": 844})
    calls = []

    def search(route):
        calls.append(route.request.url)
        if len(calls) <= 6:
            route.fulfill(json={"items": [], "next_cursor": str(len(calls))})
        else:
            route.fulfill(
                json={
                    "items": [
                        {
                            "conversation_id": "sparse-result",
                            "title": "稀疏搜索最终命中",
                            "match": "title",
                            "snippet": "稀疏搜索最终命中",
                            "highlight": [],
                            "updated_at": "2026-10-25T00:00:00Z",
                        }
                    ],
                    "next_cursor": None,
                }
            )

    page.route("**/api/v1/search?**", search)
    page.keyboard.press("Control+k")
    dialog = page.get_by_role("dialog", name="搜索对话")
    dialog.get_by_role("textbox").fill("稀疏搜索")
    continuation = dialog.get_by_role("button", name="继续搜索", exact=True)
    expect(continuation).to_be_enabled()
    assert len(calls) == 6
    expect(dialog.get_by_text("没有找到相关对话")).to_have_count(0)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(stack.log_dir / f"review-search-{'mobile' if mobile else 'desktop'}.png"))
    continuation.click()
    expect(dialog.get_by_role("option")).to_contain_text("稀疏搜索最终命中")
    assert parse_qs(urlsplit(calls[-1]).query)["cursor"] == ["6"]


def test_typing_a_new_search_clears_previous_clickable_results(logged_in):
    page = logged_in
    held = []

    def search(route):
        if "second" in route.request.url:
            held.append(route)
            return
        route.fulfill(
            json={
                "items": [{"conversation_id": "old", "title": "旧查询结果", "match": "title", "updated_at": "2026-01-01T00:00:00Z"}],
                "next_cursor": None,
            }
        )

    page.route("**/api/v1/search?**", search)
    page.keyboard.press("Control+k")
    dialog = page.get_by_role("dialog", name="搜索对话")
    query = dialog.get_by_role("textbox")
    query.fill("first")
    expect(dialog.get_by_role("option")).to_contain_text("旧查询结果")
    query.fill("second")
    with page.expect_request("**/api/v1/search?**second**"):
        expect(dialog.get_by_role("option")).to_have_count(0)
    expect(dialog.locator(".chip-spin")).to_be_visible()
    query.press("Enter")
    assert "/c/old" not in page.url
    for route in held:
        route.fulfill(json={"items": [], "next_cursor": None})


def test_library_scan_poll_never_overlaps_or_restarts_after_ready(logged_in, stack):
    page = logged_in
    pending = {
        "id": "poll-review-file",
        "filename": "轮询回归.txt",
        "status": "pending",
        "size": 5,
        "content_type": "text/plain",
        "version": 1,
        "created_at": "2026-10-25T00:00:00Z",
        "download_url": "/v1/files/poll-review-file/content",
        "meta": {},
    }
    held = []

    def files(route):
        if "ids=" in route.request.url:
            held.append(route)
        else:
            route.fulfill(json=[pending])

    page.route(re.compile(r"/api/v1/files\?"), files)
    page.route("**/api/v1/files/folders?**", lambda route: route.fulfill(json=[]))
    page.clock.install()
    page.expose_function("reviewHeldCount", lambda: len(held))
    page.goto(stack.portal + "/files")
    expect(page.locator(".lib-row", has_text=pending["filename"])).to_be_visible()
    with page.expect_request(re.compile(r"/api/v1/files\?.*ids=")):
        page.clock.run_for(2100)
    page.wait_for_function("async () => (await window.reviewHeldCount()) === 1")
    assert len(held) == 1
    page.clock.fast_forward(10_000)
    assert len(held) == 1
    held[0].fulfill(json=[{**pending, "status": "ready"}])
    expect(page.get_by_role("link", name="下载 轮询回归.txt", exact=True)).to_be_visible()
    page.clock.run_for(2500)
    assert len(held) == 1


def test_version_switch_cancels_older_page_from_previous_path(page, stack):
    from .conftest import PLATFORM, login

    conv = seed(stack, count=25, active=False)
    script = """
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run
with SessionLocal() as db:
    old = db.get(Run, RUN_ID)
    db.add(Run(user_id=old.user_id, conversation_id=old.conversation_id, parent_run_id=old.parent_run_id,
        prompt="切换后的问题", summary="切换后的回答", model="test-fast", status="succeeded"))
    db.commit()
""".replace("RUN_ID", repr(conv["run"]))
    subprocess.run([sys.executable, "-c", script], cwd=PLATFORM, env=stack.env, check=True, capture_output=True)
    login(page, stack, path="/c/" + conv["id"])
    expect(page.locator(".turn")).to_have_count(20)
    held = []
    path = "**/api/v1/conversations/" + conv["id"] + "?**before=*"
    page.route(path, lambda route: held.append(route))
    with page.expect_request(path):
        page.get_by_role("button", name="更早的消息").evaluate("el => el.click()")
    page.get_by_role("button", name="下一个版本", exact=True).last.click()
    expect(page.locator(".msg.user .bubble").last).to_have_text("切换后的问题")
    # The transport may finish an old response even after abort; it cannot mutate the selected path.
    response = page.request.get(stack.portal + f"/api/v1/conversations/{conv['id']}?limit=20")
    old_page = response.json()
    old_page["turns"] = [{**old_page["turns"][0], "id": "abandoned-page", "prompt": "旧路径不应混入", "summary": "旧回答"}]
    held[0].fulfill(json=old_page)
    expect(page.locator(".turn")).to_have_count(20)
    expect(page.get_by_text("旧路径不应混入", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="更早的消息")).to_be_enabled()


def test_historical_file_citation_opens_the_immutable_original(logged_in, stack, tmp_path):
    from .test_library_ui import _upload

    page = logged_in
    name = "历史引用回归.txt"
    _upload(page, stack, tmp_path, name, "原始引用正文".encode())
    files = page.request.get(stack.portal + "/api/v1/files").json()
    original = next(file for file in files if file["filename"] == name)
    response = page.request.post(
        stack.portal + f"/api/v1/files?parent_id={original['id']}",
        multipart={"upload": {"name": name, "mimeType": "text/plain", "buffer": "新版本正文".encode()}},
    )
    assert response.status == 201
    page.evaluate("id => window.dispatchEvent(new CustomEvent('ailab:open-file', {detail: {id}}))", original["id"])
    viewer = page.get_by_role("dialog", name=name)
    expect(viewer).to_be_visible()
    expect(viewer.locator(".viewer-plain")).to_have_text("原始引用正文", timeout=20000)
    page.screenshot(path=str(stack.log_dir / "review-historical-citation.png"))
