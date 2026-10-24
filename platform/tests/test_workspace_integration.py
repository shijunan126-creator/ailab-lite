"""Cross-branch workspace contracts on both supported SQL dialects."""

import time

from sqlalchemy import select, text

from ailab_platform.db import SessionLocal
from ailab_platform.models import Notification, QueuedMessage, Run, utcnow
from tests.test_inbox_queue import AUTH, SERVICE, conversation, detail, enqueue, finish
from tests.test_ledger_protocol import api  # noqa: F401
from tests.test_transactional_jobs import claim


def test_queue_project_instructions_and_dispatched_notification(api):  # noqa: F811
    project = api.post("/v1/projects", headers=AUTH, json={"name": "queue project"}).json()["id"]
    assert api.patch("/v1/projects/" + project, headers=AUTH, json={"instructions": "面向学生，简短回答"}).status_code == 200
    conv, run, headers = conversation(api)
    assert api.patch("/v1/conversations/" + conv, headers=AUTH, json={"project_id": project}).status_code == 200
    enqueue(api, conv)
    start = time.perf_counter()
    assert finish(api, run, headers).status_code == 200
    print(f"queue-dispatch-terminal-ms={(time.perf_counter() - start) * 1000:.3f}")
    next_run = detail(api, conv)["active_run_id"]
    assert next_run != run
    data = api.get("/internal/runs/" + next_run, headers=SERVICE).json()
    assert data["project_instructions"] == "面向学生，简短回答"
    h = {**SERVICE, "X-Run-Attempt": claim(api, next_run)["token"]}
    assert api.post("/internal/runs/" + next_run + "/status", headers=h, json={"status": "running"}).status_code == 200
    from datetime import timedelta

    with SessionLocal() as db:
        db.get(Run, next_run).created_at = utcnow() - timedelta(seconds=61)
        db.commit()
    assert finish(api, next_run, h).status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(Notification).where(Notification.run_id == next_run)).kind == "run.succeeded"


def test_confirmed_steers_bounded_checkpoint_order_and_index(api):  # noqa: F811
    conv, run, _ = conversation(api)
    with SessionLocal() as db:
        owner = db.get(Run, run).user_id
        ids = []
        for n in range(7):
            q = QueuedMessage(conversation_id=conv, user_id=owner, prompt=f"steer-{n}", model="test-fast", run_id=run, state="steered")
            db.add(q)
            db.flush()
            ids.append(q.id)
        # Confirm only six; a claimed-but-unconfirmed row must never be disclosed.
        order = list(reversed(ids[:6]))
        db.get(Run, run).runtime_meta = {"steer_ids": order}
        db.commit()
        query = (
            select(QueuedMessage.id, QueuedMessage.prompt)
            .where(QueuedMessage.run_id == run, QueuedMessage.state == "steered", QueuedMessage.id.in_(order[:5]))
            .limit(5)
        )
        pg = db.bind.dialect.name == "postgresql"
        if pg:
            db.execute(text("SET LOCAL enable_seqscan=off"))
        sql = str(query.compile(db.bind, compile_kwargs={"literal_binds": True}))
        plan = str(db.execute(text(("EXPLAIN " if pg else "EXPLAIN QUERY PLAN ") + sql)).all())
        assert "ix_queued_messages_run_state" in plan or "queued_messages_pkey" in plan or "sqlite_autoindex_queued_messages" in plan
    result = api.get("/v1/runs/" + run, headers=AUTH).json()["steered"]
    assert result == [{"id": qid, "prompt": f"steer-{5 - i}"} for i, qid in enumerate(order[:5])]
    assert detail(api, conv)["turns"][0]["steered"] == result
