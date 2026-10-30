"""Full thought history and bounded browser rendering with real persisted events."""

from __future__ import annotations

import json
import subprocess
import sys
import time

import pytest

from .conftest import PLATFORM, login
from .test_conversation_ui import seed

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize("mode,mobile", [("chat", False), ("work", False), ("chat", True), ("work", True)])
def test_full_400k_thought_across_event_pages_is_readable_with_bounded_dom(page, stack, mode, mobile):
    if mobile:
        page.set_viewport_size({"width": 390, "height": 844})
    conv = seed(stack, active=False)
    code = """
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run, RunEvent
with SessionLocal() as db:
    run = db.get(Run, RUN)
    run.options = {"mode": MODE}
    run.work_ms = 1000
    run.summary = "最终回答"
    for seq in range(1, 301):
        text = f"段落{seq:03}开头。" + "完整思考内容。" * 190 + f"段落{seq:03}结束。\\n\\n"
        db.add(RunEvent(run_id=run.id, seq=seq, type="agent.thinking", payload={"step": 0, "text": text}))
    db.commit()
""".replace("RUN", repr(conv["run"])).replace("MODE", repr(mode))
    subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, check=True)
    requests = []
    page.on("request", lambda request: requests.append(request.url) if "/events/history?" in request.url else None)
    login(page, stack, path="/c/" + conv["id"])
    head = page.locator(".steps-head")
    head.wait_for()
    assert page.locator(".turn-meta").get_by_text("详情", exact=True).count() == 0
    assert page.locator(".pr-thought-body").count() == 0
    head.click()
    thought_head = page.locator(".pr-thought > button").first
    thought_head.wait_for()
    assert thought_head.get_attribute("aria-expanded") == "false"
    assert page.locator(".pr-thought-body").count() == 0
    thought_head.click()
    page.get_by_text("段落001开头。", exact=False).wait_for()
    assert page.locator(".pr-thought-body .collapse").count() == 0
    assert page.locator(".work-timeline").evaluate("el => el.scrollTop") == 0
    page.locator(".work-timeline").hover()
    page.mouse.wheel(0, 500)
    page.wait_for_function("() => document.querySelector('.work-timeline').scrollTop > 0")
    page.locator(".work-timeline").evaluate("el => el.scrollTop = 0")
    # Traverse placeholders naturally. This loads the next event page when its sentinel approaches the viewport.
    for _ in range(14):
        page.locator(".work-timeline").evaluate("el => el.scrollTop = el.scrollHeight")
        page.wait_for_timeout(100)
        if page.get_by_text("段落300结束。", exact=False).count():
            break
    page.get_by_text("段落300结束。", exact=False).wait_for()
    assert any("after=200" in url for url in requests), requests
    assert len(requests) == 2
    mounted = page.locator(".md-chunks .viewport-block[data-mounted=true]").count()
    nodes = page.locator(".work-timeline *").count()
    assert mounted < 12, mounted
    assert nodes < 500, nodes
    # Returning to the start remounts the complete earlier text, not its truncated cache suffix.
    page.locator(".work-timeline").evaluate("el => el.scrollTop = 0")
    page.get_by_text("段落001开头。", exact=False).wait_for()
    page.screenshot(path=str(stack.log_dir / f"thought-{mode}-{'mobile' if mobile else 'desktop'}.png"))
    thought_head.click()
    assert page.locator(".pr-thought-body").count() == 0
    thought_head.click()
    page.get_by_text("段落001开头。", exact=False).wait_for()
    start = time.perf_counter()
    head.click()
    page.wait_for_function("() => !document.querySelector('.pr-thought-body')")
    collapse_ms = (time.perf_counter() - start) * 1000
    assert collapse_ms < 1000
    assert page.locator(".md-chunks .viewport-block").count() == 0
    (stack.log_dir / f"thought-{mode}-{mobile}.json").write_text(
        json.dumps({"mounted_chunks": mounted, "process_nodes": nodes, "collapse_ms": collapse_ms})
    )


def test_500_loaded_turns_keep_only_nearby_content_mounted_and_composer_responsive(page, stack):
    conv = seed(stack, count=500, active=False)
    login(page, stack, path="/c/" + conv["id"])
    page.wait_for_function("() => document.querySelectorAll('.turn').length === 20")
    for expected in range(40, 501, 20):
        page.locator(".older-turns").evaluate("el => el.click()")
        page.wait_for_function("n => document.querySelectorAll('.turn').length >= n", arg=expected)
    page.locator(".messages").evaluate("el => el.scrollTop = 0")
    page.locator("#turn-" + page.locator(".turn").first.get_attribute("id").removeprefix("turn-")).wait_for()
    page.wait_for_timeout(200)
    mounted = page.locator(".turn[data-mounted=true]").count()
    assert mounted < 20, mounted
    assert page.locator(".msg.user").count() < 20
    page.locator(".messages").evaluate("el => el.scrollTop = el.scrollHeight")
    page.get_by_text("历史消息 499", exact=True).wait_for()
    samples = []
    for _ in range(10):
        start = time.perf_counter()
        page.get_by_role("textbox", name="消息", exact=True).press_sequentially("流畅", delay=0)
        page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => resolve()))")
        samples.append((time.perf_counter() - start) * 1000)
    assert max(samples) < 1000, samples
    assert page.get_by_role("textbox", name="消息", exact=True).inner_text() == "流畅" * 10
    report = {"turns": 500, "mounted_at_top": mounted, "dom_nodes": page.locator("*").count(), "input_ms": samples}
    (stack.log_dir / "500-turns.json").write_text(json.dumps(report))
    page.screenshot(path=str(stack.log_dir / "500-turns.png"))


def test_long_answer_print_keeps_every_block_without_viewport_placeholders(page, stack, context):
    conv = seed(stack, active=False)
    code = """
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run
with SessionLocal() as db:
    run = db.get(Run, RUN)
    run.summary = "\\n\\n".join(f"导出段落{i:03}。" + "完整回答。" * 250 for i in range(300))
    db.commit()
""".replace("RUN", repr(conv["run"]))
    subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, check=True)
    context.add_init_script("window.print = () => { window.largeAnswerPrinted = true; }")
    login(page, stack, path="/c/" + conv["id"])
    page.goto(stack.portal + "/print/" + conv["id"])
    page.wait_for_function("() => window.largeAnswerPrinted === true")
    assert page.locator(".print-a p").count() == 300
    assert page.locator(".viewport-block").count() == 0
    assert "导出段落000。" in page.locator(".print-a").inner_text()
    assert "导出段落299。" in page.locator(".print-a").inner_text()


@pytest.mark.parametrize("mobile", [False, True])
def test_virtual_long_answer_preserves_web_link_side_pane_after_main_merge(page, stack, mobile):
    if mobile:
        page.set_viewport_size({"width": 390, "height": 844})
    conv = seed(stack, active=False)
    code = """
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run
with SessionLocal() as db:
    run = db.get(Run, RUN)
    run.summary = ("分析正文。" * 400 + "\\n\\n") * 10 + "[合并验收网页](https://example.com/merge-check)"
    db.commit()
""".replace("RUN", repr(conv["run"]))
    subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, check=True)
    page.route("**/api/v1/reader/render?**", lambda route: route.fulfill(content_type="text/html", body="<p>原版页面验收</p>"))
    login(page, stack, path="/c/" + conv["id"])
    url = page.url
    # Mounting estimated chunks changes scrollHeight, especially at phone width.
    # Reach a measured, visible tail before clicking; DOM presence includes overscan.
    stable = 0
    previous_height = 0
    for _ in range(40):
        metrics = page.locator(".messages").evaluate("el => { el.scrollTop = el.scrollHeight; return el.scrollHeight; }")
        page.wait_for_timeout(100)
        link = page.get_by_role("link", name="合并验收网页", exact=True)
        in_view = link.count() and link.evaluate(
            "el => { const r = el.getBoundingClientRect(); "
            "const p = el.closest('.messages').getBoundingClientRect(); return r.top >= p.top && r.bottom <= p.bottom; }"
        )
        stable = stable + 1 if metrics == previous_height and in_view else 0
        previous_height = metrics
        if stable >= 2:
            break
    assert stable >= 2, "long answer tail did not stabilize in the scroll viewport"
    page.get_by_role("link", name="合并验收网页", exact=True).click()
    page.get_by_role("tab", name="合并验收网页", exact=True).wait_for()
    assert page.get_by_role("tab", name="合并验收网页", exact=True).get_attribute("aria-selected") == "true"
    assert page.locator(".reader-frame").get_attribute("src").startswith("/api/v1/reader/render?")
    assert page.url == url
    if not mobile:
        assert page.locator(".preview-pane").bounding_box()["x"] > page.locator(".chat-main").bounding_box()["x"]
