"""The same process header survives pause, stop, failure, reload and replay."""

import re

import httpx
import pytest
from playwright.sync_api import expect

from .conftest import select_model, wait_models

pytestmark = pytest.mark.e2e


def test_one_chronological_region_stops_and_reloads_without_raw_reasoning(logged_in, stack):
    page = logged_in
    wait_models(page)
    select_model(page, "test-fast")
    page.get_by_role("textbox", name="消息", exact=True).fill("验证流式衔接")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/cnv_"), timeout=15000)
    region = page.get_by_role("region", name="工作过程")
    try:
        expect(region).to_have_count(1)
        expect(region.locator(".steps-head")).to_contain_text("正在工作中")
        # text after the last step is the answer: it streams outside the process box, not inside it
        answer = page.locator(".assistant-body > .md")
        expect(answer).to_contain_text("环境已就绪")
        assert "环境已就绪" not in region.inner_text()
        details = page.request.get(stack.portal + "/api/v1/conversations/" + page.url.rsplit("/", 1)[-1]).json()
        run = details["turns"][-1]
        assert page.request.post(stack.portal + f"/api/v1/runs/{run['id']}/cancel").ok
        expect(region.locator(".steps-head")).to_contain_text("已取消", timeout=15000)
        page.locator(".turn-meta").wait_for()
        text = region.locator(".steps-head > span").nth(1).inner_text()
        page.reload()
        page.locator(".turn-meta").wait_for()
        expect(page.locator(".steps-head > span").nth(1)).to_have_text(text)
        page.locator(".steps-head").click()
        expect(page.locator(".assistant-body > .md")).to_contain_text("环境已就绪")
    finally:
        httpx.post(stack.agent + "/e2e/release-stream", timeout=5).raise_for_status()


def test_failure_keeps_one_header_and_persisted_duration(logged_in):
    page = logged_in
    wait_models(page)
    page.get_by_role("textbox", name="消息", exact=True).fill("验证失败状态")
    page.keyboard.press("Enter")
    region = page.get_by_role("region", name="工作过程")
    expect(region.locator(".steps-head")).to_contain_text("执行失败", timeout=20000)
    page.locator(".turn-meta").wait_for()
    text = region.locator(".steps-head > span").nth(1).inner_text()
    assert "工作总时长：" in text
    page.reload()
    expect(page.locator(".steps-head > span").nth(1)).to_have_text(text)
