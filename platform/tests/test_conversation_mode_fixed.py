"""Conversation modes are fixed; fork choices and historical turn versions stay intact."""

import pytest
from sqlalchemy import select, text

from ailab_platform.api.conversation_mode import fixed_conversation_mode
from ailab_platform.db import SessionLocal
from ailab_platform.models import Conversation, Run
from tests.test_conversations import _finish
from tests.test_ledger_protocol import AUTH, api  # noqa: F401


def send(api, conv, **values):  # noqa: F811
    return api.post(f"/v1/conversations/{conv}/messages", headers=AUTH, json={"prompt": "next", **values})


def conversation(api, mode):  # noqa: F811
    conv = api.post("/v1/conversations", headers=AUTH, json={}).json()["id"]
    response = send(api, conv, mode=mode)
    assert response.status_code == 201, response.text
    return conv, response.json()


def conflict(response):
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {"code": "conversation_mode_fixed", "message": "对话模式在开始后不能切换"}


@pytest.mark.parametrize("mode,other", [("chat", "work"), ("work", "chat")])
def test_cannot_switch_mode(api, mode, other):  # noqa: F811
    conv, run = conversation(api, mode)
    _finish(api, run["id"], "done")
    conflict(send(api, conv, mode=other))


def test_omitted_mode_inherits_chat(api):  # noqa: F811
    conv, run = conversation(api, "chat")
    _finish(api, run["id"], "done")
    response = send(api, conv)
    assert response.status_code == 201, response.text
    assert response.json()["options"]["mode"] == "chat"


@pytest.mark.parametrize("values", [{"skill": "pptx"}, {"template": {"id": "unused"}}])
def test_work_inputs_cannot_switch_chat(api, values):  # noqa: F811
    conv, run = conversation(api, "chat")
    _finish(api, run["id"], "done")
    conflict(send(api, conv, **values))


@pytest.mark.parametrize("mode,other", [("chat", "work"), ("work", "chat")])
def test_queue_mode_is_fixed(api, mode, other):  # noqa: F811
    conv, run = conversation(api, mode)
    url = f"/v1/conversations/{conv}/queue"
    conflict(api.post(url, headers=AUTH, json={"prompt": "queued", "mode": other}))
    if mode == "chat":
        conflict(api.post(url, headers=AUTH, json={"prompt": "queued", "skill": "pptx"}))
    queued = api.post(url, headers=AUTH, json={"prompt": "queued"})
    assert queued.status_code == 201, queued.text
    assert queued.json()["mode"] == mode
    _finish(api, run["id"], "done")
    detail = api.get(f"/v1/conversations/{conv}", headers=AUTH).json()
    assert detail["active_run_id"] != run["id"]
    assert detail["turns"][-1]["options"]["mode"] == mode


def test_chat_fork_can_choose_work_then_is_fixed(api):  # noqa: F811
    conv, run = conversation(api, "chat")
    _finish(api, run["id"], "done")
    fork = api.post(f"/v1/conversations/{conv}/fork", headers=AUTH, json={"run_id": run["id"], "mode": "work"})
    assert fork.status_code == 201, fork.text
    branch = fork.json()["id"]
    conflict(send(api, branch, mode="chat"))
    own = send(api, branch)
    assert own.status_code == 201, own.text
    assert own.json()["options"]["mode"] == "work"
    _finish(api, own.json()["id"], "done")
    conflict(send(api, branch, mode="chat"))


@pytest.mark.parametrize("endpoint", ["edit", "retry"])
@pytest.mark.parametrize("mode,other", [("chat", "work"), ("work", "chat")])
def test_versions_keep_turn_mode(api, endpoint, mode, other):  # noqa: F811
    _, run = conversation(api, mode)
    _finish(api, run["id"], "done")
    url = f"/v1/runs/{run['id']}/{endpoint}"
    body = {"prompt": "edited"} if endpoint == "edit" else {}
    conflict(api.post(url, headers=AUTH, json={**body, "mode": other}))
    replacement = api.post(url, headers=AUTH, json=body)
    assert replacement.status_code == 201, replacement.text
    assert replacement.json()["options"]["mode"] == mode


def test_first_own_turn_wins_over_fork_and_active_leaf(api):  # noqa: F811
    conv, first = conversation(api, "chat")
    _finish(api, first["id"], "done")
    with SessionLocal() as db:
        row = db.get(Conversation, conv)
        row.forked_from = {"mode": "work"}
        leaf = Run(
            user_id=row.user_id,
            conversation_id=conv,
            parent_run_id=first["id"],
            prompt="legacy",
            options={"mode": "work"},
            status="succeeded",
        )
        db.add(leaf)
        db.flush()
        row.active_run_id = leaf.id
        db.commit()
    conflict(send(api, conv, mode="work"))
    assert send(api, conv).json()["options"]["mode"] == "chat"


def test_fixed_mode_empty_legacy_and_index_plan(api):  # noqa: F811
    assert fixed_conversation_mode(None, None) is None
    assert fixed_conversation_mode(None, {"mode": "work"}) == "work"
    conv, _ = conversation(api, "chat")
    with SessionLocal() as db:
        first = db.scalar(select(Run).where(Run.conversation_id == conv))
        first.options = {}
        assert fixed_conversation_mode(first, {"mode": "chat"}) == "work"
        if db.bind.dialect.name == "sqlite":
            plan = db.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT options FROM runs WHERE conversation_id=:id "
                    "AND JSON_EXTRACT(result, '$.forked_from_run_id') IS NULL ORDER BY created_at, id LIMIT 1"
                ),
                {"id": conv},
            ).all()
            assert any("ix_runs_conversation_created_id" in str(row) for row in plan), plan
            assert not any("TEMP B-TREE" in str(row) for row in plan), plan


@pytest.mark.parametrize("mode,other", [("chat", "work"), ("work", "chat")])
def test_schedule_conversation_uses_configured_mode_then_is_fixed(api, mode, other):  # noqa: F811
    from tests.test_schedules_citations import body

    task = api.post("/v1/schedules", headers=AUTH, json=body(mode=mode)).json()
    try:
        first = api.post(f"/v1/schedules/{task['id']}/run", headers=AUTH).json()
        _finish(api, first["run_id"], "done")
        conflict(send(api, first["conversation_id"], mode=other))
        assert api.patch(f"/v1/schedules/{task['id']}", headers=AUTH, json={"mode": other}).status_code == 200
        second = api.post(f"/v1/schedules/{task['id']}/run", headers=AUTH).json()
        assert second["conversation_id"] != first["conversation_id"]
        assert api.get(f"/v1/runs/{second['run_id']}", headers=AUTH).json()["options"]["mode"] == other
    finally:
        api.delete(f"/v1/schedules/{task['id']}", headers=AUTH)
