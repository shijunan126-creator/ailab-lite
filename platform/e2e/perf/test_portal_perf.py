"""Three paired runs per CPU rate. Budgets are reported, never hidden by assertions."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from ..conftest import PLATFORM, login
from ..test_conversation_ui import seed
from .model_data import TEXT, TRIGGER

pytestmark = pytest.mark.e2e


def percentile(values, q):
    return sorted(values)[max(0, math.ceil(len(values) * q) - 1)] if values else None


@pytest.fixture(scope="session")
def conversations(stack):
    result = [seed(stack, count=count, active=False, title=title) for count, title in [(500, "输入基准"), (20, "流式基准")]]
    code = """
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run
with SessionLocal() as db:
    for run in db.query(Run).all():
        run.options = {"mode": "chat"}
    db.commit()
"""
    subprocess.run([sys.executable, "-c", code], cwd=PLATFORM, env=stack.env, check=True)
    return result


@pytest.mark.parametrize("cpu", [1, 4])
@pytest.mark.parametrize("repeat", range(3))
def test_portal_performance(browser, stack, conversations, cpu, repeat):
    # No tracing/screenshots: recording overhead must not contaminate measurements.
    with browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="no-preference") as context:
        context.add_init_script(path=str(Path(__file__).with_name("probe.js")))
        page = context.new_page()
        cdp = context.new_cdp_session(page)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": cpu})
        login(page, stack, path="/c/" + conversations[0]["id"])
        page.wait_for_function("() => document.querySelectorAll('.turn').length === 20")
        for expected in range(40, 501, 20):
            page.locator(".older-turns").evaluate("el => el.click()")
            page.wait_for_function("n => document.querySelectorAll('.turn').length === n", arg=expected)
        page.locator(".messages").evaluate("el => el.scrollTop = el.scrollHeight")
        page.get_by_text("历史消息 499", exact=True).wait_for()
        page.wait_for_timeout(1000)
        dom = page.locator("*").count()
        page.locator("textarea").focus()
        # One key at a time ensures >= 60 distinct rendering opportunities, with 5 warmups.
        for index in range(70):
            page.keyboard.press("a")
            page.wait_for_function("n => window.perfProbe.keys.length >= n", arg=index + 1)
        keys = page.evaluate("window.perfProbe.keys.slice(5)")
        page.goto(stack.portal + "/c/" + conversations[1]["id"])
        page.locator(".turn").first.wait_for()
        page.locator(".messages").evaluate("el => el.scrollTop = el.scrollHeight")
        page.wait_for_timeout(1000)
        page.locator("textarea").fill(TRIGGER)
        previous_last = page.locator(".turn").last.get_attribute("id")
        page.evaluate("window.perfProbe.begin()")
        page.locator("textarea").press("Enter")
        # Last answer must have the full model text, not a summary or a partial delta.
        page.wait_for_function(
            "([text, previous]) => { const turn = [...document.querySelectorAll('.turn')].at(-1); "
            "return turn && turn.id !== previous && turn.textContent.includes(text); }",
            arg=[TEXT, previous_last],
            timeout=110000,
        )
        page.wait_for_timeout(250)
        stream = page.evaluate("""() => {
          const p = window.perfProbe; p.end();
          return {frames:p.frames, tasks:p.tasks, mutations:p.mutations, deltas:p.deltas,
                  previous:p.previous.length, duration:performance.now()-p.start, visible:p.visible, pending:p.pending.length};
        }""")
        deltas = stream["deltas"]
        assert len(deltas) >= 100, "not a sustained stream (or reader instrumentation broke)"
        assert stream["duration"] >= 45000, "completion matched an old turn"
        assert stream["pending"] == 0 and len(stream["visible"]) == len(deltas), "missing visibility samples"
        row = {
            "cpu": cpu,
            "repeat": repeat,
            "browser": browser.version,
            "dom": dom,
            "input_n": len(keys),
            "input_p50": percentile(keys, 0.5),
            "input_p95": percentile(keys, 0.95),
            "input_max": max(keys),
            "long_tasks": len(stream["tasks"]),
            "frame_p95": percentile(stream["frames"], 0.95),
            "visible_p95": percentile(stream["visible"], 0.95),
            "visible_max": max(stream["visible"]),
            # HLD §10: the content of turns that are not streaming changes 0 times while the answer streams. Turns
            # scrolled away swap for placeholders (bounded rendering) and changes after the last delta (the run
            # finishing re-enables edit/retry) are reported apart.
            "mutations": sum(m["t"] <= deltas[-1] and not m["mount"] for m in stream["mutations"]),
            "mount_mutations": sum(m["t"] <= deltas[-1] and m["mount"] for m in stream["mutations"]),
            "settle_mutations": sum(m["t"] > deltas[-1] for m in stream["mutations"]),
            "mutation_targets": dict(Counter(m["what"] for m in stream["mutations"]).most_common(8)),
            "previous_turns": stream["previous"],
            "stream_chars": len(TEXT),
            "stream_ms": stream["duration"],
            "delta_count": len(deltas),
            "chars_per_s": len(TEXT) / ((deltas[-1] - deltas[0]) / 1000) if len(deltas) > 1 else None,
        }
        output = Path(os.environ["AILAB_PERF_OUTPUT"])
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
