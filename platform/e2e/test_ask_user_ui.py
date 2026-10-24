"""The agent stops to ask, the person answers in the browser, and the same turn finishes on the restored workspace."""

from __future__ import annotations

import re
import subprocess
import sys

import httpx
import pytest

from .conftest import PASSWORD, USERNAME, login, wait_models

pytestmark = pytest.mark.e2e


def _token(stack) -> dict:
    token = httpx.post(stack.api + "/v1/auth/login", json={"username": USERNAME, "password": PASSWORD}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _turn(stack, headers, conversation_id: str) -> dict:
    return httpx.get(stack.api + f"/v1/conversations/{conversation_id}", headers=headers).json()["turns"][-1]


@pytest.mark.parametrize("reopen", ["live", "reload", "expired"])
def test_a_question_is_answered_in_the_browser_and_the_turn_finishes(page, stack, reopen) -> None:
    if reopen == "reload":
        page.set_viewport_size({"width": 390, "height": 844})
    login(page, stack)
    wait_models(page)
    page.fill("textarea", "需要提问：这份方案用哪个版本？")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/cnv_"), timeout=15000)
    conversation_id = page.url.rsplit("/", 1)[-1]
    headers = _token(stack)

    ask = page.locator(".ask")
    ask.wait_for(timeout=60000)
    assert ask.get_by_text("要哪个版本？").is_visible()
    # The run is parked: its sandbox is released and no agent is working on it.
    waiting = _turn(stack, headers, conversation_id)
    assert waiting["status"] == "waiting_user"
    question_id = waiting["result"]["question"]["question_id"]
    from playwright.sync_api import expect

    expect(page.locator(".steps-head")).to_contain_text("等待你回答")
    assert waiting["work_ms"] is not None

    if reopen == "expired":
        program = """
import sys
from datetime import UTC, datetime, timedelta
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run
from ailab_platform.api.reaper import expire_runtime_questions
with SessionLocal() as db:
    run = db.get(Run, sys.argv[1])
    run.waiting_since = datetime.now(UTC) - timedelta(days=4)
    db.commit()
    expire_runtime_questions(db, datetime.now(UTC))
"""
        subprocess.run([sys.executable, "-c", program, waiting["id"]], env=stack.env, check=True, timeout=20)
    if reopen != "live":
        page.reload()
        ask.get_by_role("button", name="精简版", exact=True).wait_for(timeout=15000)
    expect(page.locator(".steps-head")).to_contain_text("已暂停" if reopen == "expired" else "等待你回答")
    page.screenshot(path=str(stack.log_dir / f"ask-{reopen}.png"), full_page=True)

    page.get_by_role("button", name="精简版", exact=True).click()
    page.get_by_text("好的，按「精简版」继续完成。", exact=True).wait_for(timeout=90000)
    page.wait_for_function(
        "async (id) => (await (await fetch('/api/v1/runs/' + id)).json()).status === 'succeeded'", arg=waiting["id"], timeout=15000
    )
    assert page.get_by_text("好的，按「精简版」继续完成。", exact=True).count() == 1

    done = _turn(stack, headers, conversation_id)
    assert done["status"] == "succeeded" and done["id"] == waiting["id"]
    assert done["work_ms"] >= waiting["work_ms"]
    expect(page.locator(".steps-head")).to_contain_text("工作总时长：")
    # The same question cannot be answered again, and the answer is recorded with the question it answers.
    again = httpx.post(
        stack.api + f"/v1/runs/{waiting['id']}/answer", headers=headers, json={"question_id": question_id, "answer": "标准版"}
    )
    assert again.status_code == 409
