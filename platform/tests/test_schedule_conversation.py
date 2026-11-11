"""Scheduled tasks reply inside one conversation (ADR 0040)."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from ailab_platform.api.main import app
from ailab_platform.api.routes_schedules import dispatch_schedules, next_occurrence
from ailab_platform.contracts.schedules import scheduled_prompt
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, Notification, Run, ScheduledTask, utcnow
from tests.run_claims import SERVICE, attempt_headers
from tests.test_conversations import _finish, _other_user, _status


def as_utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def body(**kw):
    return {
        "title": "每日摘要",
        "prompt": "整理项目进展",
        "schedule": {"kind": "daily", "time": "09:00"},
        "timezone": "Asia/Shanghai",
        **kw,
    }


@pytest.fixture
def client():
    with TestClient(app) as c:
        with SessionLocal() as db:
            # dispatch is global: other tests' due tasks must not run here
            db.execute(update(ScheduledTask).values(enabled=False))
            db.commit()
        yield c, _other_user(c)


def due(task_id, at):
    with SessionLocal() as db:
        db.get(ScheduledTask, task_id).next_run_at = at
        db.commit()
    return dispatch_schedules(at)


def row(task_id):
    with SessionLocal() as db:
        task = db.get(ScheduledTask, task_id)
        db.expunge(task)
        return task


def chat_turn(c, auth, prompt="帮我看看今天的新闻", mode="chat"):
    started = c.post("/v1/conversations/start", headers=auth, json={"prompt": prompt, "mode": mode, "client_send_id": uuid.uuid4().hex})
    assert started.status_code == 201, started.text
    data = started.json()
    _status(c, data["run"]["id"], "running")
    return data["conversation"]["id"], data["run"]["id"]


def tool(c, run_id, payload, delivery=None):
    headers = {**attempt_headers(c, run_id), "X-Delivery-Id": f"{run_id}:{delivery or uuid.uuid4().hex}"}
    response = c.post(f"/internal/runs/{run_id}/schedules", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_every_occurrence_continues_the_same_conversation(client):
    c, auth = client
    task = c.post("/v1/schedules", headers=auth, json=body()).json()
    assert task["mode"] == "work" and task["conversation_id"] is None
    first = c.post(f"/v1/schedules/{task['id']}/run", headers=auth).json()
    _finish(c, first["run_id"], "第一次")
    listed = c.get("/v1/schedules", headers=auth).json()[0]
    assert listed["conversation_id"] == first["conversation_id"] and listed["conversation_title"] == "每日摘要"
    at = utcnow()
    assert due(task["id"], at) == 1
    later = row(task["id"])
    with SessionLocal() as db:
        run = db.get(Run, later.last_run_id)
        assert run.conversation_id == first["conversation_id"] and run.parent_run_id == first["run_id"]
        assert run.options["mode"] == "work" and run.options["schedule_id"] == task["id"] and run.options["client"] == "portal"
        assert run.options["schedule"]["title"] == "每日摘要" and run.options["schedule"]["rule"] == "每天 09:00"
        assert db.get(Conversation, first["conversation_id"]).active_run_id == run.id
    assert c.get(f"/v1/schedules?conversation_id={first['conversation_id']}", headers=auth).json()[0]["id"] == task["id"]


def test_a_busy_conversation_gets_the_reply_right_after_it(client):
    c, auth = client
    conv_id, run_id = chat_turn(c, auth)
    task = tool(c, run_id, {"action": "create", "title": "早报", "prompt": "汇总新闻", "schedule": {"kind": "daily", "time": "09:00"}})[
        "created"
    ]
    occurrence = utcnow()
    assert due(task["id"], occurrence) == 1  # the conversation's own turn is still running
    waiting = row(task["id"])
    assert waiting.last_status == "waiting" and as_utc(waiting.due_at) == as_utc(occurrence)
    assert as_utc(waiting.next_run_at) == as_utc(occurrence) + timedelta(minutes=1)
    assert c.get("/v1/schedules", headers=auth).json()[0]["due_at"]
    _finish(c, run_id, "好的")
    later = as_utc(waiting.next_run_at)
    assert dispatch_schedules(later) == 1
    done = row(task["id"])
    assert done.due_at is None and done.last_run_id and as_utc(done.next_run_at) > later + timedelta(hours=1)
    with SessionLocal() as db:
        run = db.get(Run, done.last_run_id)
        # the turn stands for the original time, not the retry; the conversation keeps Chat
        assert run.options["mode"] == "chat" and run.conversation_id == conv_id and run.parent_run_id == run_id
        # same tool list as the conversation's own turns (run_js follows the client), so the cached prefix holds
        assert run.options["client"] == db.get(Run, run_id).options["client"]
        local = as_utc(occurrence).astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai"))
        assert run.options["schedule"]["at"].startswith(local.strftime("%Y-%m-%d %H:%M"))


def test_a_conversation_that_stays_busy_skips_the_occurrence(client):
    c, auth = client
    _, run_id = chat_turn(c, auth)
    task = tool(c, run_id, {"action": "create", "title": "早报", "prompt": "汇总", "schedule": {"kind": "daily", "time": "09:00"}})[
        "created"
    ]
    occurrence = utcnow()
    due(task["id"], occurrence)
    late = occurrence + timedelta(hours=2)
    due(task["id"], late)
    skipped = row(task["id"])
    assert skipped.last_status == "skipped" and skipped.last_error == "会话一直在回答中" and skipped.due_at is None
    assert as_utc(skipped.next_run_at) > late
    assert c.post(f"/v1/schedules/{task['id']}/run", headers=auth).status_code == 409


def test_once_runs_a_single_time_then_finishes_quietly(client):
    c, auth = client
    today = utcnow().astimezone(__import__("zoneinfo").ZoneInfo("Asia/Shanghai")).date()
    tomorrow = (today + timedelta(days=1)).isoformat()
    task = c.post("/v1/schedules", headers=auth, json=body(schedule={"kind": "once", "time": "08:00", "date": tomorrow})).json()
    assert task["enabled"] and task["next_run_at"]
    at = as_utc(datetime.fromisoformat(task["next_run_at"].replace("Z", "+00:00")))
    assert due(task["id"], at) == 1
    with SessionLocal() as db:
        # finished: the answer stays in its conversation and the slot is free again
        assert db.get(ScheduledTask, task["id"]) is None
        conv = db.scalar(select(Conversation).where(Conversation.user_id == task["user_id"]))
        assert conv.active_run_id and not db.scalar(
            select(Notification).where(Notification.user_id == task["user_id"], Notification.kind == "schedule.paused")
        )
    assert dispatch_schedules(at + timedelta(minutes=5)) == 0


def test_a_single_task_that_cannot_start_stays_to_be_fixed(client):
    c, auth = client
    tomorrow = (utcnow() + timedelta(days=2)).date().isoformat()
    task = c.post("/v1/schedules", headers=auth, json=body(schedule={"kind": "once", "time": "08:00", "date": tomorrow})).json()
    with SessionLocal() as db:
        db.get(ScheduledTask, task["id"]).skill = "missing"
        db.commit()
    at = as_utc(datetime.fromisoformat(task["next_run_at"].replace("Z", "+00:00")))
    with SessionLocal() as db:
        db.get(ScheduledTask, task["id"]).next_run_at = at
        db.commit()
    dispatch_schedules(at + timedelta(days=1))  # its time has passed: no next occurrence
    failed = row(task["id"])
    assert not failed.enabled and failed.paused_reason == "failures" and failed.last_status == "failed"
    with SessionLocal() as db:
        stored = db.get(ScheduledTask, task["id"])
        stored.skill, stored.schedule = None, {"kind": "once", "time": "08:00", "date": "2020-01-01"}
        db.commit()
    # turning it back on needs a time that is still ahead
    refused = c.patch(f"/v1/schedules/{task['id']}", headers=auth, json={"enabled": True})
    assert refused.status_code == 422 and refused.json()["detail"] == "时间已过，请选择以后的时间"
    fixed = c.patch(
        f"/v1/schedules/{task['id']}", headers=auth, json={"enabled": True, "schedule": {"kind": "once", "time": "08:00", "date": tomorrow}}
    )
    assert fixed.status_code == 200 and fixed.json()["enabled"] and fixed.json()["paused_reason"] is None


def test_once_in_a_dst_gap_runs_at_the_first_valid_minute():
    gap = {"kind": "once", "time": "02:30", "date": "2027-03-14"}
    assert next_occurrence(gap, "America/New_York", datetime(2027, 3, 1, tzinfo=UTC)) == datetime(2027, 3, 14, 7, 30, tzinfo=UTC)
    assert next_occurrence(gap, "America/New_York", datetime(2027, 3, 15, tzinfo=UTC)) is None


def test_deleting_the_conversation_or_its_project_deletes_its_tasks(client):
    c, auth = client
    _, run_id = chat_turn(c, auth)
    conv_id = c.get(f"/v1/runs/{run_id}", headers=auth).json()["conversation_id"]
    task = tool(c, run_id, {"action": "create", "title": "早报", "prompt": "汇总", "schedule": {"kind": "daily", "time": "09:00"}})[
        "created"
    ]
    other = c.post("/v1/schedules", headers=auth, json=body()).json()
    assert c.delete(f"/v1/conversations/{conv_id}", headers=auth).status_code == 204
    assert [t["id"] for t in c.get("/v1/schedules", headers=auth).json()] == [other["id"]]
    project = c.post("/v1/projects", headers=auth, json={"name": uuid.uuid4().hex}).json()
    bound = c.post("/v1/schedules", headers=auth, json=body(project_id=project["id"])).json()
    c.post(f"/v1/schedules/{bound['id']}/run", headers=auth)
    assert c.delete(f"/v1/projects/{project['id']}", headers=auth).status_code == 204
    assert [t["id"] for t in c.get("/v1/schedules", headers=auth).json()] == [other["id"]]
    assert task["id"] not in {t["id"] for t in c.get("/v1/schedules", headers=auth).json()}


def test_the_tool_works_on_this_conversation_only(client):
    c, auth = client
    conv_id, run_id = chat_turn(c, auth)
    created = tool(
        c,
        run_id,
        {"action": "create", "title": "周报", "prompt": "汇总本周", "schedule": {"kind": "weekly", "time": "17:30", "days": [4]}},
        delivery="same",
    )
    assert created["ok"] and created["created"]["when"] == "每周五 17:30" and created["now"] and created["timezone"]
    replay = tool(c, run_id, {"action": "create", "title": "另一个", "prompt": "x", "schedule": {"kind": "daily", "time": "08:00"}}, "same")
    assert replay == created
    task = c.get("/v1/schedules", headers=auth).json()[0]
    assert task["conversation_id"] == conv_id and task["mode"] == "chat" and task["timezone"] == created["timezone"]
    elsewhere = c.post("/v1/schedules", headers=auth, json=body()).json()
    assert tool(c, run_id, {"action": "list"})["tasks"] == [created["created"]]
    refused = tool(c, run_id, {"action": "delete", "id": elsewhere["id"]})
    assert not refused["ok"] and refused["error"] == "本会话没有这个定时任务"
    changed = tool(c, run_id, {"action": "update", "id": task["id"], "schedule": {"kind": "daily", "time": "07:00"}})
    assert changed["updated"]["when"] == "每天 07:00"
    bad = tool(c, run_id, {"action": "create", "title": "坏", "prompt": "x", "schedule": {"kind": "weekly", "time": "07:00"}})
    assert bad == {**bad, "ok": False, "error": "请选择有效星期"}
    single = tool(c, run_id, {"action": "create", "title": "提醒", "prompt": "提醒开会", "schedule": {"kind": "once", "time": "00:00"}})
    assert single["ok"] and single["created"]["next"]  # no date: the next midnight
    assert tool(c, run_id, {"action": "delete", "id": task["id"]})["deleted"]["id"] == task["id"]
    assert {t["id"] for t in c.get("/v1/schedules", headers=auth).json()} == {elsewhere["id"], single["created"]["id"]}


def test_a_scheduled_turn_cannot_create_more_tasks(client):
    c, auth = client
    task = c.post("/v1/schedules", headers=auth, json=body()).json()
    run_id = c.post(f"/v1/schedules/{task['id']}/run", headers=auth).json()["run_id"]
    _status(c, run_id, "running")
    refused = tool(c, run_id, {"action": "create", "title": "再来", "prompt": "x", "schedule": {"kind": "daily", "time": "08:00"}})
    assert not refused["ok"] and refused["error"] == "定时触发的回答不能再新建定时任务"
    assert tool(c, run_id, {"action": "list"})["tasks"][0]["id"] == task["id"]


def test_the_model_reads_a_scheduled_turn_as_the_platform_speaking(client):
    c, auth = client
    task = c.post("/v1/schedules", headers=auth, json=body()).json()
    first = c.post(f"/v1/schedules/{task['id']}/run", headers=auth).json()
    _finish(c, first["run_id"], "完成")
    c.post(f"/v1/conversations/{first['conversation_id']}/messages", headers=auth, json={"prompt": "再详细一点"})
    with SessionLocal() as db:
        latest = db.scalar(select(Run).where(Run.conversation_id == first["conversation_id"]).order_by(Run.created_at.desc()).limit(1))
        schedule = db.get(Run, first["run_id"]).options["schedule"]
    history = c.get(f"/internal/runs/{latest.id}", headers=SERVICE).json()["history"]
    assert history[0]["prompt"] == scheduled_prompt("整理项目进展", schedule)
    assert history[0]["prompt"].startswith("<scheduled_task>\n用户在 ")
    assert scheduled_prompt("x", {**schedule, "manual": False}).startswith("<scheduled_task>\n定时任务「每日摘要」（每天 09:00）到点：")
    assert scheduled_prompt("x", None) == "x"
    assert schedule["manual"] is True  # the first turn above came from 立即运行
