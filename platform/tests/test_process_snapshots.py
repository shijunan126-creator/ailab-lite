"""Product-history durability independent of execution trace and model context."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select, update

from ailab_platform.api import main as main  # registers API-only transaction hooks
from ailab_platform.api.process_snapshots import derive, repair_snapshots
from ailab_platform.api.reaper import purge_deleted_conversations, purge_run_events
from ailab_platform.api.routes_runs import get_process
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.events import append_event
from ailab_platform.models import Conversation, Run, RunEvent, RunProcessSnapshot, RunStatus, User
from ailab_platform.run_state import transition_run

FIXTURE = json.loads((Path(__file__).parent / "fixtures/process_view_v1.json").read_text())


@pytest.fixture
def db():
    init_db()
    with SessionLocal() as db:
        yield db
        db.rollback()


def make_run(db, **kw):
    user = db.scalar(select(User).where(User.email == settings.dev_user_email))
    run = Run(user_id=user.id, prompt="process", status="running", **kw)
    db.add(run)
    db.commit()
    return run, user


def seed(db, run):
    for e in FIXTURE["events"]:
        append_event(db, run.id, e["type"], e["payload"], commit=False)


def test_shared_view_full_thinking_tools_and_filtered_answer(db):
    run, _ = make_run(db)
    seed(db, run)
    run.summary = "最终回答不属于过程"
    db.flush()
    items, _, _ = derive(db, run.id)
    assert items == FIXTURE["items"]
    assert items[0]["text"] == "完整思考，不会丢失。"
    assert items[3]["steps"][0]["status"] == "ok"
    db.rollback()


def test_stop_covers_late_events_and_history_survives_gc(db):
    run, user = make_run(db)
    seed(db, run)
    transition_run(db, run.id, RunStatus.SUCCEEDED, summary="最终回答不属于过程", commit=False)
    append_event(db, run.id, "agent.warning", {"message": "late"}, commit=False)
    db.commit()
    snapshot = db.get(RunProcessSnapshot, run.id)
    assert snapshot and snapshot.through_seq == len(FIXTURE["events"]) + 3
    before = get_process(run.id, user, db)
    assert before["source"] == "snapshot"
    db.execute(delete(RunEvent).where(RunEvent.run_id == run.id))
    db.commit()
    assert get_process(run.id, user, db) == before
    from ailab_platform.api.serializers import run_dict

    assert "items" not in run_dict(db, run)
    assert "items" not in run.result


def test_gc_retains_failed_projection_then_repairs(db, monkeypatch):
    from ailab_platform.api import process_snapshots as ps

    run, _ = make_run(db)
    seed(db, run)
    original = ps.derive

    def fail(*args):
        raise ValueError("projection failure")

    monkeypatch.setattr(ps, "derive", fail)
    transition_run(db, run.id, RunStatus.FAILED)
    old = datetime.now(UTC) - timedelta(days=90)
    db.execute(update(Run).where(Run.id == run.id).values(finished_at=old))
    db.execute(update(RunEvent).where(RunEvent.run_id == run.id).values(created_at=old))
    db.commit()
    assert db.get(RunProcessSnapshot, run.id) is None
    purge_run_events(db, datetime.now(UTC))
    assert db.scalar(select(RunEvent.id).where(RunEvent.run_id == run.id).limit(1))
    monkeypatch.setattr(ps, "derive", original)
    monkeypatch.setattr(ps, "_repair_cursor", run.id[:-1])
    repair_snapshots(db)
    assert db.get(RunProcessSnapshot, run.id)
    purge_run_events(db, datetime.now(UTC))
    assert db.scalar(select(RunEvent.id).where(RunEvent.run_id == run.id).limit(1)) is None


def test_resume_child_visibility_fallback_and_cascade(db):
    run, user = make_run(db)
    append_event(db, run.id, "agent.thinking", {"step": 0, "text": "first"})
    transition_run(db, run.id, RunStatus.INTERRUPTED)
    first_seq = db.get(RunProcessSnapshot, run.id).through_seq
    transition_run(db, run.id, RunStatus.QUEUED)
    transition_run(db, run.id, RunStatus.RUNNING)
    append_event(db, run.id, "agent.thinking", {"step": 1, "text": "second"})
    transition_run(db, run.id, RunStatus.SUCCEEDED)
    assert db.get(RunProcessSnapshot, run.id).through_seq > first_seq
    child, _ = make_run(db, retry_of=run.id, parent_run_id=run.id)
    transition_run(db, child.id, RunStatus.CANCELED)
    assert db.get(RunProcessSnapshot, child.id).items == []
    assert db.get(RunProcessSnapshot, run.id).items
    stranger = User(id="process-stranger", email="stranger@process.test", display_name="B")
    with pytest.raises(HTTPException) as exc:
        get_process(run.id, stranger, db)
    assert exc.value.status_code == 404
    db.execute(delete(RunProcessSnapshot).where(RunProcessSnapshot.run_id == run.id))
    db.commit()
    assert get_process(run.id, user, db)["source"] == "events"
    assert db.get(RunProcessSnapshot, run.id)
    db.execute(delete(RunEvent).where(RunEvent.run_id == run.id))
    db.execute(delete(Run).where(Run.id == run.id))
    db.commit()
    assert db.get(RunProcessSnapshot, run.id, populate_existing=True) is None


def test_size_refusal_retains_events(db, monkeypatch):
    from ailab_platform.api import process_snapshots as ps

    run, _ = make_run(db)
    append_event(db, run.id, "agent.thinking", {"step": 0, "text": "full text"})
    monkeypatch.setattr(ps, "MAX_INPUT_BYTES", 1)
    transition_run(db, run.id, RunStatus.CANCELED)
    assert db.get(RunProcessSnapshot, run.id) is None
    assert db.scalar(select(RunEvent.id).where(RunEvent.run_id == run.id).limit(1))
    with pytest.raises(HTTPException) as exc:
        get_process(run.id, db.get(User, run.user_id), db)
    assert exc.value.status_code == 413


def test_conversation_purge_cascades_process(db):
    user = db.scalar(select(User).where(User.email == settings.dev_user_email))
    conv = Conversation(user_id=user.id, title="purge process", deleted_at=datetime.now(UTC) - timedelta(days=90))
    db.add(conv)
    db.commit()
    run, _ = make_run(db, conversation_id=conv.id)
    transition_run(db, run.id, RunStatus.CANCELED)
    assert db.get(RunProcessSnapshot, run.id)
    db.execute(delete(RunEvent).where(RunEvent.run_id == run.id))
    db.commit()
    purge_deleted_conversations(db, datetime.now(UTC))
    assert db.get(RunProcessSnapshot, run.id, populate_existing=True) is None


def test_snapshot_never_changes_runtime_context_state(db):
    run, user = make_run(db, runtime_meta={"context_snapshot": {"messages": [{"role": "user", "content": "frozen prefix"}]}})
    before = json.dumps(run.runtime_meta, sort_keys=True)
    seed(db, run)
    transition_run(db, run.id, RunStatus.FAILED)
    get_process(run.id, user, db)
    assert json.dumps(run.runtime_meta, sort_keys=True) == before


def test_recorded_model_requests_unchanged_by_snapshot_create_read_update(db):
    import asyncio

    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.model_transport import ModelTransport
    from tests.prefix_harness import scripted_server
    from tests.test_agent_loop import FakeBroker, FakePlatform

    run, user = make_run(db)

    def record():
        with scripted_server([{"content": "done"}]) as (url, requests):
            asyncio.run(
                AgentLoop(platform=FakePlatform(), broker=FakeBroker(), llm=ModelTransport(url)).process("fixture", attempt_id="attempt")
            )
        return requests

    before = record()
    seed(db, run)
    transition_run(db, run.id, RunStatus.INTERRUPTED)
    get_process(run.id, user, db)
    transition_run(db, run.id, RunStatus.QUEUED)
    transition_run(db, run.id, RunStatus.RUNNING)
    append_event(db, run.id, "agent.warning", {"message": "snapshot update"})
    transition_run(db, run.id, RunStatus.FAILED)
    get_process(run.id, user, db)
    assert record() == before


def test_many_thinking_chunks_exceed_legacy_result_tail_without_loss(db):
    run, _ = make_run(db)
    chunks = [f"思考第 {i} 段：" + "完整内容。" * 100 for i in range(80)]
    for chunk in chunks:
        append_event(db, run.id, "agent.thinking", {"step": 1, "text": chunk}, commit=False)
    transition_run(db, run.id, RunStatus.SUCCEEDED)
    snapshot = db.get(RunProcessSnapshot, run.id)
    assert len(snapshot.items) == 1
    assert snapshot.items[0]["text"] == "".join(chunks)
    assert len(snapshot.items[0]["text"]) > 20000


def test_process_http_contract_and_cross_user_404(db):
    from fastapi.testclient import TestClient

    from ailab_platform.db import hash_token
    from ailab_platform.models import ApiToken, new_id

    run, _ = make_run(db)
    seed(db, run)
    transition_run(db, run.id, RunStatus.SUCCEEDED, summary="最终回答不属于过程")
    other = User(email=new_id("other") + "@process.test", display_name="Other")
    db.add(other)
    db.flush()
    token = new_id("process-token")
    db.add(ApiToken(user_id=other.id, name="process-test", token_hash=hash_token(token)))
    db.commit()
    with TestClient(main.app) as client:
        response = client.get(f"/v1/runs/{run.id}/process", headers={"Authorization": "Bearer test-token"})
        assert response.status_code == 200, response.text
        assert response.json() == {"schema_version": 2, "source": "snapshot", "items": FIXTURE["items"]}
        denied = client.get(f"/v1/runs/{run.id}/process", headers={"Authorization": "Bearer " + token})
        assert denied.status_code == 404
        detail = client.get(f"/v1/runs/{run.id}", headers={"Authorization": "Bearer test-token"}).json()
        assert "process" not in detail and "items" not in detail


@pytest.mark.parametrize("status", [RunStatus.CANCELED, RunStatus.FAILED, RunStatus.INTERRUPTED])
@pytest.mark.parametrize("summary", ["", "confirmed answer"])
def test_stopped_stream_retains_unconfirmed_visible_answer_without_context_side_effects(db, status, summary):
    run, user = make_run(db, summary=summary, runtime_meta={"context_snapshot": {"messages": [{"role": "user", "content": "frozen"}]}})
    original_context = json.dumps(run.runtime_meta, sort_keys=True)
    append_event(db, run.id, "agent.delta", {"step": 0, "text": "visible "}, commit=False)
    append_event(db, run.id, "agent.delta", {"step": 0, "text": "partial answer"}, commit=False)
    transition_run(db, run.id, status)
    before = get_process(run.id, user, db)
    assert before["source"] == "snapshot"
    assert before["items"] == [{"kind": "text", "step": 0, "text": "visible partial answer"}]
    assert run.summary == summary
    assert json.dumps(run.runtime_meta, sort_keys=True) == original_context
    db.execute(delete(RunEvent).where(RunEvent.run_id == run.id))
    db.commit()
    assert get_process(run.id, user, db) == before
    assert run.summary == summary


def test_resumed_partial_survives_full_gc_and_late_append(db):
    from ailab_platform.api.routes_runtime import queue_continuation

    run, user = make_run(db)
    append_event(db, run.id, "agent.message", {"step": 0, "content": "old saved progress"})
    transition_run(db, run.id, RunStatus.INTERRUPTED, summary="old saved progress")
    queue_continuation(db, run)
    db.commit()
    transition_run(db, run.id, RunStatus.RUNNING)
    append_event(db, run.id, "agent.delta", {"step": 1, "text": "new partial"})
    append_event(db, run.id, "agent.tool_call", {"step": 1, "name": "exec", "arguments": {"cmd": "sleep 30"}})
    append_event(db, run.id, "agent.tool_result", {"step": 1, "name": "exec", "ok": True, "running": True, "job_key": "job"})
    transition_run(db, run.id, RunStatus.CANCELED)
    before = get_process(run.id, user, db)
    assert "new partial" in json.dumps(before)
    assert "仍在运行" not in json.dumps(before, ensure_ascii=False)
    assert "已取消" in json.dumps(before, ensure_ascii=False)
    snapshot_seq = db.get(RunProcessSnapshot, run.id).through_seq
    db.execute(delete(RunEvent).where(RunEvent.run_id == run.id))
    db.commit()
    assert get_process(run.id, user, db) == before
    late = append_event(db, run.id, "agent.warning", {"message": "late"})
    assert late.seq == snapshot_seq + 1
    items, seq, _ = derive(db, run.id)
    assert seq == late.seq
    assert "new partial" in json.dumps(items) and "late" in json.dumps(items)


def test_shared_process_lifecycle_fixture():
    from ailab_platform.api.process_view import reconcile_terminal, reduce_event

    fixture = json.loads((Path(__file__).parent / "fixtures/process_lifecycle_v1.json").read_text())
    items = []
    for event in fixture["events"]:
        reduce_event(items, event["type"], event["payload"])
    assert reconcile_terminal(items, fixture["status"]) == fixture["items"]


@pytest.mark.parametrize("gc", [False, True])
def test_legacy_snapshot_upgrades_without_erasing_gc_history(db, gc):
    run, user = make_run(db, summary="old summary")
    append_event(db, run.id, "agent.delta", {"step": 1, "text": "recoverable new partial"})
    transition_run(db, run.id, RunStatus.CANCELED)
    snapshot = db.get(RunProcessSnapshot, run.id)
    snapshot.schema_version = 1
    snapshot.items = [{"kind": "steps", "steps": [{"name": "exec", "label": "运行命令 · 仍在运行", "status": "note"}]}]
    db.commit()
    if gc:
        db.execute(delete(RunEvent).where(RunEvent.run_id == run.id))
        db.commit()
    view = get_process(run.id, user, db)
    assert view["schema_version"] == 2
    if gc:
        assert view["items"][0]["steps"][0]["label"] == "运行命令 · 已取消"
    else:
        assert "recoverable new partial" in json.dumps(view)
    assert db.get(RunProcessSnapshot, run.id).schema_version == 2
