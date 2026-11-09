"""Whole-system review regressions with real API, versions, guards and transactions."""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.api.routes_memory import memory_for
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.memory_dream.document import apply_ops
from ailab_platform.memory_recall.document import maintenance_needed, used_chars
from ailab_platform.memory_recall.invalidations import for_handles
from ailab_platform.memory_recall.lineage import forget_conversations
from ailab_platform.memory_recall.retention import purge_closed
from ailab_platform.memory_recall.store import replace_entries
from ailab_platform.models import Conversation, MemoryDreamRun, MemoryItem, Run, User, utcnow
from tests.run_claims import attempt_headers
from tests.test_memory_hardening import dream_row, put
from tests.test_memory_recall import _conversation, _running, _seed, _uid
from tests.test_memory_summary import project
from tests.test_skill_settings import _user


def tool(c, me, rid, delivery, **body):
    return c.post(f"/internal/runs/{rid}/memory", headers={**attempt_headers(c, rid), "X-Delivery-Id": rid + ":" + delivery}, json=body)


def test_shared_tools_write_account_and_preserve_kind_protection():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        pid = project(c, me)
        rid = _running(c, me)
        with SessionLocal() as db:
            current = db.get(Run, rid)
            conv = Conversation(user_id=uid, project_id=pid, title="shared")
            db.add(conv)
            db.flush()
            current.conversation_id = conv.id
            db.commit()
        add = tool(c, me, rid, "a", action="add", kind="preference", content="先给结论", explicit=True)
        assert add.status_code == 200, add.text
        old = add.json()["entries"][0]
        replace = tool(c, me, rid, "b", action="replace", id=old["id"], content="先列风险")
        assert replace.status_code == 200, replace.text
        current = replace.json()["entries"][0]
        assert current["project_id"] is None and current["origin"] == "explicit"
        with SessionLocal() as db:
            row = db.get(MemoryItem, current["id"])
            assert row.kind == "preference" and row.created_by == "correction"
            resolved = for_handles(db, uid, [old["id"]], memory_for(db.get(User, uid)), None, "shared")
            assert resolved[old["id"]]["current"]["id"] == row.id
            assert resolved[old["id"]]["change_type"] == "correction"


def test_redacted_and_expired_ancestors_keep_correction_redirects():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid)
        rid = _seed(uid, "private ancestor", conversation_id=conv)
        old, new = "mem_o" + uid[4:20], "mem_n" + uid[4:20]
        with SessionLocal() as db:
            owner = db.get(User, uid)
            replace_entries(
                db,
                owner,
                [
                    {
                        "id": old,
                        "kind": "fact",
                        "origin": "inferred",
                        "content": "private ancestor",
                        "source_quote": "private ancestor",
                        "evidence_run_ids": [rid],
                        "source": "nightly",
                        "invalid_at": (utcnow() - timedelta(days=100)).isoformat(),
                        "superseded_by": new,
                        "superseded_reason": "correction",
                    },
                    {"id": new, "kind": "fact", "content": "current truth", "origin": "inferred"},
                ],
            )
            db.commit()
            forget_conversations(db, owner, [conv])
            db.commit()
            row = db.get(MemoryItem, old)
            assert row and row.content == "" and not row.source_quote and row.source_run_ids == []
            assert for_handles(db, uid, [old], memory_for(owner), None, "shared")[old]["current"]["id"] == new
            row.content = "retained old text"
            db.commit()
            purge_closed(db, utcnow())
            assert db.get(MemoryItem, old).content == ""


def test_merge_duplicate_becomes_condense_redirect_and_carries_review():
    with TestClient(app) as c:
        me = _user(c)
        pid = project(c, me, "isolated")
        doc = put(
            c,
            me,
            [
                {"content": "duplicate"},
                {"content": "duplicate", "project_id": pid},
                {"content": "待确认", "project_id": pid, "suppressed": True},
            ],
        )
        project_row = next(e for e in doc["entries"] if e["project_id"] == pid and e["content"] == "duplicate")
        assert c.patch("/v1/projects/" + pid, headers=me, json={"memory_mode": "shared"}).status_code == 200
        with SessionLocal() as db:
            row = db.get(MemoryItem, project_row["id"])
            assert row.status == "closed" and row.superseded_by and row.superseded_reason == "condense"
        review = c.get("/v1/memory/view", headers=me).json()["review"]
        assert review[0]["content"] == "待确认" and review[0]["project_id"] is None


def test_maintenance_probe_is_read_only_and_inferred_multisource_can_evict(monkeypatch):
    monkeypatch.setattr(settings, "memory_doc_max_chars", 1000)
    owner = SimpleNamespace(settings={"memory": {"revision": 7, "tombstones": [{"hash": "x"}]}})
    lines = [{"id": "mem_" + str(i), "kind": "fact", "origin": "explicit", "content": str(i) + "长" * 180} for i in range(7)]
    before = json.dumps(owner.settings)
    assert not maintenance_needed(lines, owner)
    assert json.dumps(owner.settings) == before
    for e in lines:
        e.update(origin="inferred", evidence_run_ids=["run_a", "run_b"])
    after, audit = apply_ops(lines, lines, [], [], owner, None)
    assert used_chars(after) <= 1000 and audit


def test_review_add_requires_confirmation_and_settings_read_does_not_load_document(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        put(c, me, [{"kind": "preference", "content": "待确认偏好", "suppressed": True}])
        rid = _running(c, me)
        response = tool(c, me, rid, "r1", action="add", kind="preference", content="待确认偏好")
        assert response.json()["awaiting_confirmation"] and not response.json()["entries"]
        response = tool(c, me, rid, "r2", action="add", kind="preference", content="待确认偏好", explicit=True)
        assert response.status_code == 200, response.text
        assert not c.get("/v1/memory/view", headers=me).json()["review"]

        def forbidden(*a, **kw):
            raise AssertionError("settings must not compile all scopes")

        monkeypatch.setattr("ailab_platform.api.routes_memory.memory_for", forbidden)
        out = c.get("/v1/memory/settings", headers=me)
        assert out.status_code == 200 and set(out.json()) == {"enabled", "auto_save", "reference_history", "dream"}


def test_concurrent_target_edit_drops_only_that_op(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        doc = put(c, me, [{"content": "旧甲", "origin": "inferred"}, {"content": "旧乙", "origin": "inferred"}])
        a, b = doc["entries"]
        rid = _seed(uid, "新甲，新乙")

        async def chat(*args):
            current = c.get("/v1/memory", headers=me).json()
            assert (
                c.patch(
                    "/v1/memory/items/" + a["id"], headers=me, json={"expected_revision": current["revision"], "content": "用户并发修改"}
                ).status_code
                == 200
            )
            return json.dumps(
                {
                    "ops": [
                        {"op": "update", "id": e["id"], "content": text, "quote": text, "run": rid}
                        for e, text in [(a, "新甲"), (b, "新乙")]
                    ]
                }
            ), {}

        monkeypatch.setattr(service, "_chat", chat)
        with SessionLocal() as db:
            job = dream_row(uid)
            db.add(job)
            db.commit()
            identity = job.id
        asyncio.run(service.process_dream_chain(identity))
        result = c.get("/v1/memory/dreams/" + identity, headers=me).json()
        assert result["status"] == "applied", result
        assert {e["content"] for e in c.get("/v1/memory", headers=me).json()["entries"]} == {"用户并发修改", "新乙"}
        with SessionLocal() as db:
            assert service.metrics(db, uid)["changes"] == 1
            assert service.list_runs(db, uid)[0].report["changed"] == 1


def test_protected_only_overcap_is_skipped_without_model_or_unlocked_settings_write(monkeypatch):
    monkeypatch.setattr(settings, "memory_doc_max_chars", 1000)
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        put(c, me, [{"content": str(i) + "长" * 180, "origin": "explicit"} for i in range(6)])

        async def forbidden(*args):
            raise AssertionError("protected-only maintenance must not call model")

        monkeypatch.setattr(service, "_chat", forbidden)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            identity = row.id
        asyncio.run(service.process_dream(identity))
        with SessionLocal() as db:
            assert db.get(MemoryDreamRun, identity).status == "skipped"
            # prepare() runs outside the user lock, so it must not rewrite user.settings (a racing write would lose tombstones)
            assert "nothing_condensable" not in db.get(User, uid).settings["memory"]


def test_review_input_can_be_activated_without_invisible_suppression():
    from ailab_platform.contracts.memory import MemoryEntryInput

    row = MemoryEntryInput(content="paused", status="suppressed")
    assert row.status == "review" and row.suppressed is False
    row = MemoryEntryInput(content="paused", suppressed=True)
    assert row.status == "review" and row.suppressed is False


def test_maintenance_no_progress_signature_prevents_another_paid_call(monkeypatch):
    monkeypatch.setattr(settings, "memory_doc_max_chars", 1000)
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        put(c, me, [{"content": str(i) + "长" * 180, "origin": "chat"} for i in range(6)])
        calls = []

        async def chat(*args):
            calls.append(1)
            return '{"ops":[]}', {}

        monkeypatch.setattr(service, "_chat", chat)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            identity = row.id
        asyncio.run(service.process_dream_chain(identity))
        assert len(calls) == 1
        assert c.post("/v1/memory/dream/run", headers=me).json()["status"] == "nothing"
        assert len(calls) == 1
