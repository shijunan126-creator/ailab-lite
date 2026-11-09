"""Review regressions: state adapters, line fences, no-ops and closed-source privacy."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.api.routes_memory import memory_for
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.memory_dream.maintenance import save
from ailab_platform.memory_recall.document import maintenance_needed
from ailab_platform.memory_recall.lineage import forget_conversations
from ailab_platform.memory_recall.store import replace_entries
from ailab_platform.models import MemoryItem, User, utcnow
from tests.run_claims import attempt_headers
from tests.test_memory_hardening import dream_row, put
from tests.test_memory_recall import _conversation, _running, _seed, _uid
from tests.test_memory_summary import edit, project, scripted
from tests.test_skill_settings import _user


@pytest.mark.parametrize("path", ["patch", "delete", "add", "replace", "remove", "save", "dream", "edit", "undo", "merge", "switch"])
def test_writes_with_suppressed_row(path, monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        paused, target = "mem_p" + uid[4:20], "mem_t" + uid[4:20]
        pid = project(c, me, "isolated" if path == "merge" else "shared") if path in {"merge", "switch"} else None
        put(
            c,
            me,
            [{"id": paused, "content": "暂停行", "suppressed": True}, {"id": target, "content": "活动行", "project_id": pid}],
        )
        current = c.get("/v1/memory", headers=me).json()
        if path in {"patch", "delete"}:
            response = (
                c.patch(f"/v1/memory/items/{target}", headers=me, json={"expected_revision": current["revision"], "content": "修改"})
                if path == "patch"
                else c.delete(f"/v1/memory/items/{target}?expected_revision={current['revision']}", headers=me)
            )
            assert response.status_code == 200, response.text
        elif path in {"add", "replace", "remove"}:
            rid = _running(c, me)
            response = c.post(
                f"/internal/runs/{rid}/memory",
                headers={**attempt_headers(c, rid), "X-Delivery-Id": rid + ":review"},
                json={"action": path, "id": target, "content": "工具写入", "explicit": True},
            )
            assert response.status_code == 200, response.text
            if path == "replace":
                stale = c.post(
                    f"/internal/runs/{rid}/memory",
                    headers={**attempt_headers(c, rid), "X-Delivery-Id": rid + ":stale"},
                    json={"action": "remove", "id": target, "explicit": True},
                )
                assert stale.status_code == 422 and "list" in stale.text
        elif path == "dream":
            rid = _seed(uid, "本轮有效证据")

            async def chat(*args):
                return json.dumps({"ops": [{"op": "add", "kind": "fact", "content": "后台新增", "quote": "本轮有效证据", "run": rid}]}), {}

            monkeypatch.setattr(service, "_chat", chat)
            with SessionLocal() as db:
                row = dream_row(uid)
                db.add(row)
                db.commit()
                did = row.id
            asyncio.run(service.process_dream_chain(did))
            assert c.get("/v1/memory/dreams/" + did, headers=me).json()["status"] == "applied"
        elif path in {"edit", "undo"}:
            scripted(monkeypatch, ops=[{"op": "add", "kind": "fact", "content": "新增行"}])
            result = edit(c, me)
            assert result["status"] == "applied", result
            if path == "undo":
                response = c.post(f"/v1/memory/dreams/{result['id']}/revert", headers=me)
                assert response.status_code == 200, response.text
        elif path in {"merge", "switch"}:
            response = c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "shared" if path == "merge" else "isolated"})
            assert response.status_code == 200, response.text
        else:
            with SessionLocal() as db:
                user = db.get(User, uid)
                before = memory_for(user)
                after = save(db, user, before, before["entries"])
                assert after["revision"] == before["revision"]
                db.commit()
        assert next(e for e in c.get("/v1/memory", headers=me).json()["entries"] if e["id"] == paused)["status"] == "suppressed"


def test_noop_and_scope_signature_ignore_global_revision():
    from ailab_platform.agent.context import ContextManager
    from ailab_platform.agent.prompts import memory_prompt

    document = {"revision": 1, "entries": [{"id": "mem_a", "kind": "fact", "content": "事实"}]}
    other = {**document, "revision": 99}
    assert memory_prompt(document) == memory_prompt(other)
    assert ContextManager._memory_signature(document) == ContextManager._memory_signature(other)


def test_protected_overcap_does_not_call_manual_dream(monkeypatch):
    monkeypatch.setattr(settings, "memory_doc_max_chars", 1000)
    with TestClient(app) as c:
        me = _user(c)
        put(c, me, [{"content": str(i) + "长" * 180, "origin": "explicit"} for i in range(6)])
        response = c.post("/v1/memory/dream/run", headers=me)
        assert response.json()["status"] == "nothing", response.text
        with SessionLocal() as db:
            user = db.get(User, _uid(c, me))
            assert user.settings["memory"]["nothing_condensable"]
        assert maintenance_needed([{"id": "mem_i", "kind": "fact", "content": "长" * 1200, "origin": "inferred"}])


def test_closed_versions_are_redacted_on_forget():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid)
        rid = _seed(uid, "PRIVATE_SOURCE", conversation_id=conv)
        with SessionLocal() as db:
            user = db.get(User, uid)
            replace_entries(
                db,
                user,
                [
                    {
                        "id": "mem_old",
                        "kind": "fact",
                        "origin": "inferred",
                        "source": "nightly",
                        "content": "PRIVATE_SOURCE",
                        "source_quote": "PRIVATE_SOURCE",
                        "evidence_run_ids": [rid],
                        "invalid_at": utcnow().isoformat(),
                    }
                ],
            )
            db.commit()
            forget_conversations(db, user, [conv])
            db.commit()
            assert db.get(MemoryItem, "mem_old") is None


def test_no_signal_context_does_not_reprocess_current(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid)
        prev = _seed(uid, "数据库建议？", "建议PostgreSQL", days_ago=2, conversation_id=conv)
        current = _seed(uid, "行，就这么定了", days_ago=1, conversation_id=conv)
        calls = []

        async def chat(*args):
            calls.append(1)
            return json.dumps(
                {
                    "ignored_runs": [{"run": prev, "reason": "no_signal"}],
                    "ops": [{"op": "add", "kind": "project", "content": "采用PostgreSQL", "quote": "行，就这么定了", "run": current}],
                }
            ), {}

        monkeypatch.setattr(service, "_chat", chat)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream_chain(did))
        assert len(calls) == 1
        assert c.get("/v1/memory", headers=me).json()["entries"][0]["content"] == "采用PostgreSQL"


def test_edit_of_condensed_row_records_correction():
    from ailab_platform.memory_recall.invalidations import for_handles

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        identity = "mem_" + uid[4:20]
        put(c, me, [{"id": identity, "content": "合并结果", "created_by": "condense", "origin": "inferred"}])
        rev = c.get("/v1/memory", headers=me).json()["revision"]
        assert (
            c.patch("/v1/memory/items/" + identity, headers=me, json={"expected_revision": rev, "content": "用户更正"}).status_code == 200
        )
        with SessionLocal() as db:
            user = db.get(User, uid)
            row = db.get(MemoryItem, identity)
            assert row.created_by == "condense" and row.superseded_reason == "correction"
            resolved = for_handles(db, uid, [identity], memory_for(user), None, "shared")
            assert resolved[identity]["change_type"] == "correction"


def test_merge_keeps_newest_fifty_sources():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        pid = project(c, me, "isolated")
        runs = [_seed(uid, "source " + str(i), days_ago=i) for i in range(52)]
        put(
            c,
            me,
            [
                {"content": "相同事实", "evidence_run_ids": runs[:26]},
                {"content": "相同事实", "project_id": pid, "evidence_run_ids": runs[26:]},
            ],
        )
        response = c.patch("/v1/projects/" + pid, headers=me, json={"memory_mode": "shared"})
        assert response.status_code == 200, response.text
        sources = c.get("/v1/memory", headers=me).json()["entries"][0]["evidence_run_ids"]
        assert set(sources) == set(runs[:50])
