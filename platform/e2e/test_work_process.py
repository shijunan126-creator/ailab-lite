"""The same process header survives pause, stop, failure, reload and replay."""

import itertools
import re

import httpx
import pytest
from playwright.sync_api import expect

from .conftest import select_model, wait_models
from .scripted_agent import ANSWER_TEXT, COLLAPSE_TRIGGER

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


def test_thought_folds_when_the_answer_starts_without_a_frame_jump(logged_in, stack):
    page = logged_in
    page.set_viewport_size({"width": 900, "height": 600})
    wait_models(page)
    select_model(page, "test-fast")
    # A completed turn above makes the conversation genuinely scrollable.
    page.get_by_role("textbox", name="消息", exact=True).fill("先建立一段历史")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/c/cnv_"), timeout=15000)
    page.locator(".turn-meta").wait_for(timeout=15000)
    page.get_by_role("textbox", name="消息", exact=True).fill(COLLAPSE_TRIGGER)
    page.keyboard.press("Enter")
    region = page.get_by_role("region", name="工作过程").last
    head = region.locator(".steps-head")
    thought = region.locator(".pr-thought > button").first
    try:
        # The thought being written is open without a click, and the process box is open around it.
        expect(head).to_have_attribute("aria-expanded", "true")
        expect(thought).to_have_attribute("aria-expanded", "true")
        expect(region.locator(".pr-thought-body")).to_be_visible()
        assert page.locator(".messages").evaluate("el => el.scrollTop") > 0
        # Record every frame from the release until the fold has had time to finish.
        page.evaluate(
            """() => {
                const el = document.querySelector('.messages');
                const turn = [...el.querySelectorAll('.turn')].at(-1);
                const assistant = turn.querySelector('.msg.assistant');
                const process = turn.querySelector('.work-process');
                const button = process.querySelector('.pr-thought > button');
                window.__ailabFrames = [];
                let folded = -1;
                const tick = () => {
                    const box = el.getBoundingClientRect();
                    window.__ailabFrames.push({
                        t: performance.now(),
                        assistantTop: assistant.getBoundingClientRect().top - box.top,
                        process: process.getBoundingClientRect().height,
                    });
                    if (folded < 0 && button.getAttribute('aria-expanded') === 'false') folded = window.__ailabFrames.length;
                    if (folded < 0 ? window.__ailabFrames.length < 600 : window.__ailabFrames.length < folded + 40)
                        requestAnimationFrame(tick);
                    else window.__ailabDone = true;
                };
                requestAnimationFrame(tick);
            }"""
        )
        httpx.post(stack.agent + "/e2e/release-answer", timeout=5).raise_for_status()
        expect(page.locator(".assistant-body > .md").last).to_have_text(ANSWER_TEXT)
        expect(thought).to_have_attribute("aria-expanded", "false")
        expect(region.locator(".pr-thought-body")).to_have_count(0)
        # Only the thought folds: the process box stays open while the run works.
        expect(head).to_have_attribute("aria-expanded", "true")
        page.wait_for_function("() => window.__ailabDone === true")
        frames = page.evaluate("() => window.__ailabFrames")
        heights = [frame["process"] for frame in frames]
        drop = heights[0] - heights[-1]
        assert drop > 100, heights
        # A glide passes through several heights, and no frame carries a large share of the whole move.
        assert len({round(h) for h in heights if heights[-1] + 1 < h < heights[0] - 1}) > 4, heights
        for key in ("process", "assistantTop"):
            step = max(abs(b[key] - a[key]) for a, b in itertools.pairwise(frames))
            assert step < drop * 0.3, (key, step, frames)
    finally:
        httpx.post(stack.agent + "/e2e/release-answer", timeout=5).raise_for_status()
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
