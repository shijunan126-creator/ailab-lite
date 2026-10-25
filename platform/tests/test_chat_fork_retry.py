"""Fork/retry contracts including real storage ownership and concurrent public requests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.events import append_event
from ailab_platform.models import Artifact, Conversation, FileObject, ModelUsage, OutboxMessage, Run, RunEvent, utcnow
from ailab_platform.storage import get_storage
from tests.run_claims import SERVICE, attempt_headers
from tests.test_conversations import AUTH, _finish, _other_user, _status


def create(c, modes=("chat",)):
    conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
    turns = []
    for index, mode in enumerate(modes):
        r = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": f"prompt-{index}", "mode": mode}).json()
        _finish(c, r["id"], f"answer-{index}")
        turns.append(r)
    return conv, turns


def test_fork_copies_history_artifacts_usage_summary_and_independent_objects():
    with TestClient(app) as c:
        conv, turns = create(c, ("work", "chat"))
        storage = get_storage()
        first = turns[0]["id"]
        storage.put_bytes(f"previews/{first}/1/p-01.jpg", b"jpeg")
        storage.put_bytes(f"workspaces/{first}.tgz", b"old snapshot")
        with SessionLocal() as db:
            old = db.get(Run, first)
            old.result = {
                "workspace_saved": True,
                "previews": [{"filename": "a", "pages": ["1/p-01.jpg"]}],
            }
            db.add(ModelUsage(run_id=first, user_id=old.user_id, prompt_tokens=20, completion_tokens=10))
            db.get(Conversation, conv["id"]).summary = "remember"
            db.get(Conversation, conv["id"]).summary_through_run_id = first
            file = FileObject(user_id=old.user_id, filename="a", size=4, status="ready", storage_key="unused")
            db.add(file)
            db.flush()
            file_id = file.id
            db.add(Artifact(run_id=old.id, file_id=file.id))
            append_event(db, old.id, "agent.thinking", {"step": 0, "text": "last reasoning", "seconds": 1})
        # An active later turn does not prevent an earlier fork.
        c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "busy"})
        bad = c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": first, "mode": "chat"})
        assert bad.status_code == 422
        response = c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": first, "mode": "work"})
        assert response.status_code == 201, response.text
        fork = response.json()
        assert fork["forked_from"] == {"conversation_id": conv["id"], "run_id": first, "mode": "work"}
        detail = c.get(f"/v1/conversations/{fork['id']}", headers=AUTH).json()
        copied = detail["turns"][0]
        assert len(detail["turns"]) == 1 and copied["usage"]["prompt_tokens"] == 20
        assert copied["artifacts"][0]["file_id"] == file_id
        with SessionLocal() as db:
            assert db.get(Conversation, fork["id"]).summary_through_run_id == copied["id"]
            assert db.get(Run, copied["id"]).result["forked_from_run_id"] == first
            assert db.scalar(select(ModelUsage.id).where(ModelUsage.run_id == copied["id"])) is None
            assert db.get(OutboxMessage, copied["id"]) is None
        # Physically remove source objects and source process, beyond the soft-delete guarantee.
        c.delete(f"/v1/conversations/{conv['id']}", headers=AUTH)
        storage.delete_prefix(f"previews/{first}/")
        storage.delete(f"workspaces/{first}.tgz")
        with SessionLocal() as db:
            for e in db.scalars(select(RunEvent).where(RunEvent.run_id == first)):
                db.delete(e)
            db.commit()
        assert c.get(f"/v1/runs/{copied['id']}/previews/1/p-01.jpg", headers=AUTH).content == b"jpeg"
        events = c.get(f"/v1/runs/{copied['id']}/events/history", headers=AUTH).json()
        assert any(e["payload"].get("text") == "last reasoning" for e in events)
        new = c.post(f"/v1/conversations/{fork['id']}/messages", headers=AUTH, json={"prompt": "continue"}).json()
        key = c.get(f"/internal/runs/{new['id']}/model-input", headers=SERVICE).json()["workspace_restore_key"]
        assert storage.get_bytes(key) == b"old snapshot"


@pytest.mark.parametrize("status", ["succeeded", "failed", "canceled"])
def test_retry_is_atomic_and_excluded_from_history_and_workspace(status):
    with TestClient(app) as c:
        conv, turns = create(c, ("work", "work"))
        prior, old = (r["id"] for r in turns)
        with SessionLocal() as db:
            db.get(Run, old).status = status
            for id_ in (prior, old):
                db.get(Run, id_).result = {"workspace_saved": True}
            db.get(Conversation, conv["id"]).summary = "includes old answer"
            db.get(Conversation, conv["id"]).summary_through_run_id = old
            db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: c.post(f"/v1/runs/{old}/retry", headers=AUTH, json={}), range(2)))
        assert sorted(r.status_code for r in results) == [201, 409]
        new = next(r.json() for r in results if r.status_code == 201)
        assert new["retry_of"] == old and new["prompt"] == turns[-1]["prompt"]
        detail = c.get(f"/v1/conversations/{conv['id']}", headers=AUTH).json()
        assert [r["id"] for r in detail["turns"]] == [prior, new["id"]]
        assert c.get(f"/v1/runs/{old}", headers=AUTH).json()["superseded_by_run_id"] == new["id"]
        bootstrap = c.get(f"/internal/runs/{new['id']}/model-input", headers=SERVICE).json()
        assert bootstrap["workspace_restore_key"] == f"workspaces/{prior}.tgz"
        history = c.get(f"/internal/runs/{new['id']}/model-context", headers=SERVICE).json()
        assert history["through_run_id"] is None and [r["id"] for r in history["history"]] == [prior]
        assert c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": old, "mode": "work"}).status_code == 422
        assert c.post(f"/v1/runs/{prior}/retry", headers=AUTH, json={}).status_code == 409
        assert c.post(f"/v1/runs/{new['id']}/retry", headers=AUTH, json={}).json()["detail"] == {
            "code": "conversation_busy",
            "message": "上一条消息还在回答中，请稍候或先停止它",
        }
        assert c.post(f"/v1/runs/{old}/retry", headers=_other_user(c), json={}).status_code == 404


def test_title_compare_and_set_is_immediate_and_does_not_override_rename():
    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "question"}).json()
        headers = attempt_headers(c, run["id"])
        _status(c, run["id"], "running")
        url = f"/internal/runs/{run['id']}/conversation-title"
        assert c.put(url, headers=headers, json={"title": "概括问题"}).json() == {"ok": True}
        history = c.get(f"/v1/runs/{run['id']}/events/history", headers=AUTH).json()
        assert history[-1]["type"] == "conversation.titled"
        assert history[-1]["payload"] == {"conversation_id": conv["id"], "title": "概括问题"}
        c.patch(f"/v1/conversations/{conv['id']}", headers=AUTH, json={"title": "manual"})
        assert c.put(url, headers=headers, json={"title": "late"}).json() == {"ok": False}
        assert c.get(f"/v1/conversations/{conv['id']}", headers=AUTH).json()["title"] == "manual"
        assert c.put(url, headers=AUTH, json={"title": "no"}).status_code == 403


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_thinking_history_never_compacts_or_strips_deltas(mode):
    with TestClient(app) as c:
        _conv, turns = create(c, (mode,))
        id_ = turns[0]["id"]
        texts = ["first", "最后一块" * 10000]
        with SessionLocal() as db:
            for text in texts:
                append_event(db, id_, "agent.thinking", {"step": 0, "text": text, "seconds": 0.2})
        events = c.get(f"/v1/runs/{id_}/events/history", headers=AUTH).json()
        assert [e["payload"]["text"] for e in events if e["type"] == "agent.thinking"] == texts


def test_fork_failure_rolls_back_database_and_cleans_copied_objects(monkeypatch):
    from sqlalchemy import func

    with TestClient(app) as c:
        conv, turns = create(c, ("chat", "chat"))
        storage = get_storage()
        for run in turns:
            storage.put_bytes(f"previews/{run['id']}/1/p-01.jpg", b"preview")
        with SessionLocal() as db:
            before = db.scalar(select(func.count()).select_from(Conversation))
        copy = storage.copy
        destinations = []

        def broken(src, dst):
            destinations.append(dst)
            if len(destinations) == 2:
                raise OSError("injected failure")
            copy(src, dst)

        monkeypatch.setattr(storage, "copy", broken)
        with pytest.raises(OSError, match="injected failure"):
            c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": turns[-1]["id"], "mode": "chat"})
        assert all(not storage.exists(key) for key in destinations)
        with SessionLocal() as db:
            assert db.scalar(select(func.count()).select_from(Conversation)) == before
        assert all(storage.exists(f"previews/{r['id']}/1/p-01.jpg") for r in turns)


def test_fork_chat_to_work_and_expired_checkpoint_workspace():
    with TestClient(app) as c:
        conv, turns = create(c)
        for mode in ("chat", "work"):
            assert (
                c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": turns[0]["id"], "mode": mode}).status_code
                == 201
            )
        conv2, turns2 = create(c, ("work",))
        with SessionLocal() as db:
            old = db.get(Run, turns2[0]["id"])
            old.status = "canceled"
            old.runtime_meta = {
                "workspace": {"key": f"workspaces/{old.id}/snapshot.tgz", "saved_at": (utcnow() - timedelta(days=4)).timestamp()}
            }
            db.commit()
        response = c.post(f"/v1/conversations/{conv2['id']}/fork", headers=AUTH, json={"run_id": turns2[0]["id"], "mode": "work"})
        assert response.status_code == 422 and "过期" in response.json()["detail"]


def test_fork_and_retry_queries_have_conversation_index_plans():
    from sqlalchemy import text, tuple_

    from ailab_platform.db import init_db

    init_db()
    with SessionLocal() as db:
        common = select(Run.id).where(Run.conversation_id == "for-explain", Run.superseded_by_run_id.is_(None))
        queries = [
            common.order_by(Run.created_at.desc(), Run.id.desc()).limit(1),
            common.where(tuple_(Run.created_at, Run.id) <= (utcnow(), "run_cursor")).order_by(Run.created_at, Run.id).limit(501),
        ]
        for query in queries:
            sql = str(query.compile(db.bind, compile_kwargs={"literal_binds": True}))
            if db.bind.dialect.name == "postgresql":
                db.execute(text("SET LOCAL enable_seqscan = off"))
                rows = db.execute(text("EXPLAIN " + sql))
            else:
                rows = db.execute(text("EXPLAIN QUERY PLAN " + sql))
            plan = " ".join(str(tuple(row)) for row in rows)
            assert "ix_runs_conversation" in plan


def test_fork_rejects_legacy_workspace_that_was_pruned_by_later_turn():
    with TestClient(app) as c:
        conv, turns = create(c, ("work", "work"))
        with SessionLocal() as db:
            old = db.get(Run, turns[0]["id"])
            old.result = {"workspace_saved": False}
            db.commit()
        response = c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": turns[0]["id"], "mode": "work"})
        assert response.status_code == 422 and "不可用" in response.json()["detail"]


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "canceled", "deleted"])
def test_late_title_is_fenced_normalized_and_streamed_before_end(terminal):
    from ailab_platform.api.routes_runs import _poll_events

    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "question"}).json()
        rid = run["id"]
        owner = attempt_headers(c, rid)
        _status(c, rid, "running")
        _status(c, rid, "succeeded" if terminal == "deleted" else terminal)
        if terminal == "deleted":
            c.delete(f"/v1/conversations/{conv['id']}", headers=AUTH)
        events, status, pending = _poll_events(rid, 0)
        assert status in {"succeeded", "failed", "canceled"}
        assert pending == (terminal in {"succeeded", "failed"})
        title_url = f"/internal/runs/{rid}/conversation-title"
        markdown = "# 你好！这是**离线演示模型**的回答 __重点__ *星号* `代码` [链接](https://example.com)\n  空白 "
        expected = "你好！这是离线演示模型的回答 重点 星号 代码 链接 空白"
        result = c.put(title_url, headers=owner, json={"title": markdown})
        assert result.status_code == 200, result.text
        assert result.json() == {"ok": terminal in {"succeeded", "failed"}}
        assert c.put(title_url, headers={**owner, "X-Run-Attempt": "foreign"}, json={"title": "foreign"}).status_code == 409
        if terminal in {"succeeded", "failed"}:
            assert c.get(f"/v1/conversations/{conv['id']}", headers=AUTH).json()["title"] == expected
            late, _, pending = _poll_events(rid, events[-1][0])
            assert not pending and late[-1][1:] == ("conversation.titled", {"conversation_id": conv["id"], "title": expected})
            stream = c.get(f"/v1/runs/{rid}/events", headers=AUTH).text
            assert stream.index("event: conversation.titled") < stream.index("event: stream.end")
        # Ending durable ownership rejects even the once-valid attempt.
        with SessionLocal() as db:
            from ailab_platform.models import RunAttempt

            db.get(RunAttempt, owner["X-Run-Attempt"]).ended_at = utcnow()
            db.commit()
        assert c.put(title_url, headers=owner, json={"title": "stale"}).status_code == 409
        assert c.get(f"/internal/runs/{rid}/state", headers=owner).status_code == 409


def test_retry_and_titled_fork_do_not_request_another_title():
    with TestClient(app) as c:
        conv, turns = create(c)
        c.patch(f"/v1/conversations/{conv['id']}", headers=AUTH, json={"title": "已有标题"})
        fork = c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": turns[0]["id"], "mode": "chat"}).json()
        assert fork["title"] == "已有标题 · 分支"
        fork_run = c.post(f"/v1/conversations/{fork['id']}/messages", headers=AUTH, json={"prompt": "next"}).json()
        retry = c.post(f"/v1/runs/{turns[0]['id']}/retry", headers=AUTH, json={}).json()
        for run in (fork_run, retry):
            assert c.get(f"/internal/runs/{run['id']}/model-input", headers=SERVICE).json()["needs_conversation_title"] is False


def test_terminal_sse_title_grace_expires_without_worker_cleanup():
    from ailab_platform.api.routes_runs import _poll_events

    with TestClient(app) as c:
        _conv, turns = create(c)
        rid = turns[0]["id"]
        assert _poll_events(rid, 0)[2] is True
        with SessionLocal() as db:
            db.get(Run, rid).finished_at = utcnow() - timedelta(seconds=11)
            db.commit()
        assert _poll_events(rid, 0)[2] is False


def test_canceled_run_cannot_title_its_live_conversation():
    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "question"}).json()
        owner = attempt_headers(c, run["id"])
        _status(c, run["id"], "canceled")
        assert c.put(f"/internal/runs/{run['id']}/conversation-title", headers=owner, json={"title": "late"}).json() == {"ok": False}
        assert c.get(f"/v1/conversations/{conv['id']}", headers=AUTH).json()["title"] == ""


def test_title_cas_between_terminal_poll_reads_cannot_be_lost_at_stream_end(monkeypatch):
    from sqlalchemy.orm import Session

    from ailab_platform.api.routes_runs import _poll_events

    with TestClient(app) as c:
        conv, turns = create(c)
        rid = turns[0]["id"]
        owner = attempt_headers(c, rid)
        events, _, _ = _poll_events(rid, 0)
        original = Session.scalar
        injected = False

        def scalar(db, statement, *args, **kwargs):
            nonlocal injected
            if not injected and "JOIN run_attempts" in str(statement):
                injected = True
                response = c.put(f"/internal/runs/{rid}/conversation-title", headers=owner, json={"title": "竞态标题"})
                assert response.json() == {"ok": True}
            return original(db, statement, *args, **kwargs)

        monkeypatch.setattr(Session, "scalar", scalar)
        late, _, pending = _poll_events(rid, events[-1][0])
        assert injected and not pending
        assert late[-1][1:] == ("conversation.titled", {"conversation_id": conv["id"], "title": "竞态标题"})


@pytest.mark.parametrize(
    ("source_title", "first_title", "expected"),
    [("原标题", "首轮", "原标题 · 分支"), ("", "首轮", "首轮 · 分支"), ("", "", ""), ("长" * 199, "首轮", ("长" * 199 + " · 分支")[:200])],
)
def test_fork_title_uses_conversation_then_first_turn_and_preserves_empty(source_title, first_title, expected):
    with TestClient(app) as c:
        conv, turns = create(c)
        with SessionLocal() as db:
            db.get(Conversation, conv["id"]).title = source_title
            db.get(Run, turns[0]["id"]).title = first_title
            db.commit()
        response = c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": turns[0]["id"], "mode": "chat"})
        assert response.status_code == 201, response.text
        assert response.json()["title"] == expected


def test_work_branch_of_chat_history_cannot_fork_back_to_chat():
    with TestClient(app) as c:
        conv, turns = create(c, ("chat", "chat"))
        response = c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": turns[-1]["id"], "mode": "work"})
        assert response.status_code == 201, response.text
        fork = response.json()
        assert fork["forked_from"]["mode"] == "work"
        detail = c.get(f"/v1/conversations/{fork['id']}", headers=AUTH).json()
        assert detail["forked_from"]["mode"] == "work"
        assert all(turn["options"]["mode"] == "chat" for turn in detail["turns"])
        for turn in detail["turns"]:
            url = f"/v1/conversations/{fork['id']}/fork"
            assert c.post(url, headers=AUTH, json={"run_id": turn["id"], "mode": "chat"}).status_code == 422
            assert c.post(url, headers=AUTH, json={"run_id": turn["id"], "mode": "work"}).status_code == 201
