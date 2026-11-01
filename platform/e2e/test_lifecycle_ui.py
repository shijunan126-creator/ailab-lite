"""Trash files can be read and restored in place; schedules show their end date and why the platform paused them."""

import subprocess
import sys
from datetime import date, timedelta

import pytest
from playwright.sync_api import expect

from .conftest import PLATFORM

pytestmark = pytest.mark.e2e


def test_trashed_file_previews_and_restores(logged_in, stack):
    page = logged_in
    uploaded = page.request.post(
        stack.portal + "/api/v1/files", multipart={"upload": {"name": "回收站预览.txt", "mimeType": "text/plain", "buffer": b"trash"}}
    )
    assert uploaded.status == 201, uploaded.text()
    fid = uploaded.json()["id"]
    assert page.request.delete(stack.portal + f"/api/v1/files/{fid}").status == 204
    page.goto(stack.portal + "/files?c=trash")
    page.get_by_role("button", name="回收站预览.txt", exact=True).click()
    viewer = page.get_by_role("dialog", name="回收站预览.txt")
    expect(viewer).to_be_visible()
    expect(viewer.get_by_role("link", name="围绕 回收站预览.txt 对话")).to_have_count(0)
    expect(viewer.get_by_role("button", name="分享 回收站预览.txt")).to_have_count(0)
    viewer.get_by_role("button", name="恢复", exact=True).click()
    expect(viewer).to_have_count(0)
    expect(page.locator(".lib-row", has_text="回收站预览.txt")).to_have_count(0)
    assert page.request.get(stack.portal + f"/api/v1/files/{fid}").json()["deleted_at"] is None


def test_schedule_end_date_and_paused_reason(logged_in, stack):
    page = logged_in
    page.goto(stack.portal + "/schedules")
    page.get_by_role("button", name="新建", exact=True).click()
    dialog = page.get_by_role("dialog")
    dialog.get_by_label("名称", exact=True).fill("有结束日期的任务")
    dialog.get_by_label("要做什么", exact=True).fill("每天汇总")
    dialog.get_by_role("button", name="结束", exact=True).click()
    page.get_by_role("listbox", name="结束", exact=True).get_by_role("option", name="一周后", exact=True).click()
    with page.expect_response(lambda r: r.url.endswith("/api/v1/schedules") and r.request.method == "POST") as response:
        dialog.get_by_role("button", name="保存", exact=True).click()
    assert response.value.status == 201, response.value.text()
    task = response.value.json()
    ends = date.today() + timedelta(days=7)
    assert task["ends_on"] == ends.isoformat()
    row = page.locator(".mrow", has_text="有结束日期的任务")
    expect(row).to_contain_text(f"{ends.month}月{ends.day}日结束")
    code = f"""
from ailab_platform.db import SessionLocal
from ailab_platform.models import ScheduledTask
with SessionLocal() as db:
    task = db.get(ScheduledTask, {task["id"]!r})
    task.enabled, task.paused_reason = False, "unread"
    db.commit()
"""
    subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, check=True)
    page.reload()
    expect(row).to_contain_text("结果一直没人看，已暂停")
    row.get_by_role("switch").click()
    expect(row).to_contain_text("下次")
    assert page.request.delete(stack.portal + f"/api/v1/schedules/{task['id']}").status == 204
