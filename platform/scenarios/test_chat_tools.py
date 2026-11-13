"""Chat with the tools a plain question reaches for: charts, the web, the clock, schedules and memory.

These cover both the tools every request carries and the ones loaded on demand, so a change to how the catalog is
offered shows up here as a failed task or as more calls and tokens per task, not only as a smaller first request.
"""

import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from scenarios.conftest import contracts


def test_chart_answer_compares_two_series(api):
    _, turn = api.start(
        "两个产品四个季度的营收（万元）：A 产品 120、135、128、160；B 产品 80、95、110、130。对比一下走势，哪个增长更快？",
        mode="chat",
    )
    contracts(turn)
    blocks = re.findall(r"```openui(.*?)```", turn.reply, re.S)
    assert blocks, turn.reply
    assert any(re.search(r"\b(LineChart|BarChart|ComboChart)\(", block) for block in blocks), blocks
    # B grows 62.5% against A's 33.3%; the data must be the user's, not invented
    assert "B" in turn.reply and all(str(n) in "".join(blocks) for n in (120, 160, 80, 130)), turn.reply


def test_web_question_cites_a_source(api):
    _, turn = api.start("查一下 Python 3.13.0 是哪天正式发布的，给出来源。", mode="chat")
    contracts(turn)
    assert "web_search" in turn.tools(), turn.tools()
    assert "2024" in turn.reply and re.search(r"10\s*月|October|-10-", turn.reply), turn.reply
    assert "](http" in turn.reply, turn.reply


def test_today_comes_from_the_clock(api):
    _, turn = api.start("今天几号，星期几？", mode="chat")
    contracts(turn)
    assert "current_time" in turn.tools(), turn.tools()
    today = datetime.now(ZoneInfo("Asia/Shanghai"))
    assert re.search(rf"{today.month}\s*月\s*{today.day}\s*[日号]|{today:%Y-%m-%d}", turn.reply), turn.reply


def test_recurring_reminder_becomes_a_schedule(api):
    _, turn = api.start("每个工作日早上 9 点提醒我看销售日报。", mode="chat")
    contracts(turn)
    assert "schedule" in turn.tools(), turn.tools()
    created = [c for c in turn.tool_calls("schedule") if (c.get("arguments") or {}).get("action") == "create"]
    assert created, turn.tool_calls("schedule")
    rule = created[-1]["arguments"].get("schedule") or {}
    assert rule.get("kind") == "weekdays" and rule.get("time") in {"09:00", "9:00"}, rule
    # deleting the conversation (api fixture) drops its schedules


def test_memory_is_saved_and_forgotten_on_request(api):
    marker = "北极星" + uuid.uuid4().hex[:4]
    try:
        cid, first = api.start(f"记住：我负责的项目代号是「{marker}」，下个月上线。", mode="chat")
        contracts(first)
        assert "memory" in first.tools(), first.tools()
        assert any(marker in e["content"] for e in api.memory_entries()), "not saved"
        second = api.ask(cid, "把刚才让你记的项目代号那条忘掉。", mode="chat")
        contracts(second, later=True)
        left = [e for e in api.memory_entries() if marker in e["content"]]
        assert not left, left
    finally:
        api.forget(marker)  # the account must not keep a test fact when an assertion above failed
