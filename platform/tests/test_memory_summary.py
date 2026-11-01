"""Summary/edit/merge contracts exercise the real API, write path and fenced jobs."""

import asyncio
import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.api.routes_memory import memory_for
from ailab_platform.contracts.memory import MemoryEntryInput
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.memory_dream.prompts import EDIT_SYSTEM, EXTRACT_SYSTEM, JUDGE_SYSTEM, MERGE_SYSTEM, SUMMARY_SYSTEM
from ailab_platform.memory_dream.summary import digest, regenerate, scope_entries, summary_row
from ailab_platform.memory_recall.policy import model_view
from ailab_platform.models import MemoryDreamRun, MemorySummary, Project, User
from tests.run_claims import attempt_headers
from tests.test_memory_hardening import put
from tests.test_memory_recall import _running, _seed, _uid
from tests.test_skill_settings import SERVICE, _user


@pytest.fixture
def api():
    with TestClient(app) as client:
        me = _user(client)
        yield client, me, _uid(client, me)


def scripted(monkeypatch, *, ops=None, decisions=None, before=None):
    calls = []

    async def chat(messages, model, user):
        system = messages[0]["content"]
        calls.append(system)
        if before:
            await asyncio.to_thread(before, system, user)
        if system == EDIT_SYSTEM:
            return json.dumps({"ops": ops or [], "reply": "已按要求更新。"}, ensure_ascii=False), {}
        if system == MERGE_SYSTEM:
            return json.dumps({"decisions": decisions or []}), {}
        return "### 偏好\n你喜欢简洁的中文回答。", {}

    monkeypatch.setattr(service, "_chat", chat)
    return calls


def project(c, me, mode="shared"):
    pid = c.post("/v1/projects", headers=me, json={"name": __import__("uuid").uuid4().hex}).json()["id"]
    if mode != "shared":
        assert c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": mode}).status_code == 200
    return pid


def edit(c, me, instruction="记住我喜欢中文", pid=None):
    response = c.post("/v1/memory/summary/edit", headers=me, json={"project_id": pid, "instruction": instruction})
    assert response.status_code == 202, response.text
    return c.get(f"/v1/memory/dreams/{response.json()['id']}", headers=me).json()


@pytest.mark.parametrize("mode,account", [(None, True), ("shared", True), ("isolated", False)])
def test_visibility_matrix_bootstrap_and_live_tools(api, mode, account):
    c, me, _uid_value = api
    pid = project(c, me, mode or "shared") if mode else None
    conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
    entries = [{"kind": "user", "content": "中文回答", "id": "mem_account"}]
    if pid:
        entries.append({"kind": "user", "content": "中文项目", "project_id": pid, "id": "mem_project"})
    put(c, me, entries)
    rid = c.post(f"/v1/conversations/{conv}/messages", headers=me, json={"prompt": "中文"}).json()["id"]
    expected = ({"mem_account"} if account else set()) | ({"mem_project"} if pid else set())
    assert {e["id"] for e in c.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()["memory"]["entries"]} == expected
    headers = attempt_headers(c, rid)
    c.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"})
    for action in ("list", "search"):
        out = c.post(f"/internal/runs/{rid}/memory", headers=headers, json={"action": action, "query": "中文"})
        assert out.status_code == 200, out.text
        assert {e["id"] for e in out.json()["entries"]} == expected


@pytest.mark.parametrize("mode", [None, "shared", "isolated"])
def test_history_pool_search_and_direct_read(api, mode):
    c, me, uid = api
    shared = project(c, me)
    isolated = c.post("/v1/projects", headers=me, json={"name": "隔离项目"}).json()["id"]
    c.patch(f"/v1/projects/{isolated}", headers=me, json={"memory_mode": "isolated"})
    sources = {}
    for pid in (None, shared, isolated):
        conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
        sources[pid] = _seed(uid, f"BLUEFIN 业务报告 {pid or '账号'}", conversation_id=conv)
    current = shared if mode == "shared" else isolated if mode else None
    conv = c.post("/v1/conversations", headers=me, json={"project_id": current}).json()["id"]
    rid = c.post(f"/v1/conversations/{conv}/messages", headers=me, json={"prompt": "BLUEFIN"}).json()["id"]
    out = c.post(f"/internal/runs/{rid}/recall", headers=SERVICE, json={"query": "BLUEFIN"}).json()
    allowed = {sources[isolated]} if mode == "isolated" else {sources[None], sources[shared]}
    assert {e["run_id"] for e in out["results"]} == allowed
    for source in sources.values():
        assert c.post(f"/internal/runs/{rid}/recall", headers=SERVICE, json={"run_id": source}).status_code == (
            200 if source in allowed else 404
        )


def test_digest_stale_empty_and_auto_save_off(api, monkeypatch):
    c, me, _uid_value = api
    calls = scripted(monkeypatch)
    assert not c.get("/v1/memory/summary", headers=me).json()["stale"]
    assert c.post("/v1/memory/dream/run", headers=me).json()["status"] == "nothing"
    assert not calls
    assert not c.get("/v1/memory/summary", headers=me).json()["stale"]
    assert c.post("/v1/memory/dream/run", headers=me).json()["status"] == "nothing"
    doc = put(c, me, [{"kind": "user", "content": "中文回答"}], auto_save=False)
    assert c.post("/v1/memory/dream/run", headers=me).status_code == 202
    summary = c.get("/v1/memory/summary", headers=me).json()
    assert summary["text"].startswith("###") and not summary["stale"] and summary["entries"] == 1
    assert len(calls) == 1
    with SessionLocal() as db:
        generated = summary_row(db, _uid_value, None).generated_at
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=UTC)
        assert datetime.fromisoformat(summary["generated_at"]) == generated
    changed_metadata = [{**doc["entries"][0], "confidence": 0.5}]
    put(
        c,
        me,
        [{k: v for k, v in changed_metadata[0].items() if k in MemoryEntryInput.model_fields}],
        auto_save=False,
    )
    assert not c.get("/v1/memory/summary", headers=me).json()["stale"]
    assert digest(doc["entries"]) == digest(list(reversed(doc["entries"])))


def test_edit_replace_revert_restores_summary_and_scope(api, monkeypatch):
    c, me, uid = api
    pid = project(c, me)
    doc = put(c, me, [{"kind": "user", "content": "中文回答"}, {"content": "项目旧偏好", "project_id": pid}])
    with SessionLocal() as db:
        entries = scope_entries(db.get(User, uid), pid)
        db.add(
            MemorySummary(
                user_id=uid, project_id=pid, text="### 原摘要\n你有旧偏好。", source_digest=digest(entries), generated_at=datetime.now(UTC)
            )
        )
        db.commit()
    target = doc["entries"][1]["id"]
    calls = scripted(
        monkeypatch, ops=[{"op": "replace", "id": target, "content": "项目新偏好"}, {"op": "remove", "id": doc["entries"][0]["id"]}]
    )
    result = edit(c, me, pid=pid)
    assert result["status"] == "applied" and result["report"]["reply"]
    entries = c.get("/v1/memory", headers=me).json()["entries"]
    assert entries[0]["content"] == "中文回答" and entries[1]["content"] == "项目新偏好"
    assert len(calls) == 2
    assert c.post(f"/v1/memory/dreams/{result['id']}/revert", headers=me).json()["status"] == "reverted"
    restored = c.get("/v1/memory/summary", headers=me, params={"project_id": pid}).json()
    assert restored["text"] == "### 原摘要\n你有旧偏好。" and not restored["stale"]
    assert c.get("/v1/memory", headers=me).json()["entries"][1]["project_id"] == pid


def test_edit_add_secret_refusal_conflict_and_active(api, monkeypatch):
    c, me, uid = api
    scripted(monkeypatch, ops=[{"op": "add", "kind": "user", "content": "中文回答"}])
    assert edit(c, me)["status"] == "applied"
    scripted(monkeypatch, ops=[{"op": "add", "content": "我的密码是 SuperSecret123!"}])
    refused = edit(c, me)
    assert refused["status"] == "skipped" and "口令" in refused["report"]["reply"]
    assert len(c.get("/v1/memory", headers=me).json()["entries"]) == 1

    def change(system, user):
        if system == EDIT_SYSTEM:
            with SessionLocal() as db:
                owner = db.get(User, uid)
                memory = dict(owner.settings["memory"])
                memory["entries"] = [{**e, "content": "竞争修改事实"} for e in memory["entries"]]
                owner.settings = {**owner.settings, "memory": {**memory, "revision": memory["revision"] + 1}}
                db.commit()

    scripted(monkeypatch, ops=[{"op": "add", "content": "不会写入"}], before=change)
    conflict = edit(c, me)
    assert conflict["status"] == "failed" and conflict["error"] == "记忆已变化，请重试"
    with SessionLocal() as db:
        db.add(MemoryDreamRun(user_id=uid, local_date="2026-11-01", timezone="Asia/Shanghai", status="pending", trigger="manual"))
        db.commit()
    response = c.post("/v1/memory/summary/edit", headers=me, json={"instruction": "忘记"})
    assert response.status_code == 409 and response.json()["detail"] == "正在整理记忆，请稍后再试"


def test_clear_scope_redacts_tombstones_and_all_statuses(api, monkeypatch):
    c, me, uid = api
    pid = project(c, me)
    put(
        c,
        me,
        [
            {"content": "账号保留"},
            {"content": "项目暂停", "project_id": pid, "suppressed": True},
            {"content": "项目待确认", "project_id": pid, "status": "review"},
            {"content": "项目失效", "project_id": pid, "invalid_at": datetime.now(UTC).isoformat()},
        ],
    )
    response = c.delete("/v1/memory/entries", headers=me, params={"project_id": pid})
    assert response.status_code == 200 and response.json()["entries"] == 0 and response.json()["text"] == ""
    assert [e["content"] for e in c.get("/v1/memory", headers=me).json()["entries"]] == ["账号保留"]
    with SessionLocal() as db:
        assert len(db.get(User, uid).settings["memory"]["tombstones"]) == 3
        assert summary_row(db, uid, pid) is None
    assert c.delete("/v1/memory/entries", headers=me).json()["entries"] == 0


def test_summary_wait_has_no_transaction_lock_and_digest_race(api, monkeypatch):
    c, me, uid = api
    put(c, me, [{"content": "中文回答"}])

    def change(system, user):
        with SessionLocal() as db:
            owner = db.get(User, uid)
            doc = memory_for(owner)
            from ailab_platform.api.routes_memory import write_memory
            from ailab_platform.contracts.memory import MemoryEntryInput, MemoryUpdate
            from ailab_platform.locks import user_critical_section

            with user_critical_section(db, uid):
                write_memory(
                    db, owner, MemoryUpdate(expected_revision=doc["revision"], entries=[MemoryEntryInput(content="新的回答偏好")]), "user"
                )
                db.commit()

    scripted(monkeypatch, before=change)
    asyncio.run(regenerate(uid, None))
    with SessionLocal() as db:
        assert summary_row(db, uid, None) is None


def test_shared_to_isolated_moves_only_complete_project_evidence(api):
    c, me, uid = api
    pid = project(c, me)
    conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
    local = _seed(uid, "项目来源", conversation_id=conv)
    outside = _seed(uid, "账号来源")
    doc = put(
        c,
        me,
        [
            {"content": "完整来源", "evidence_run_ids": [local]},
            {"content": "混合来源", "evidence_run_ids": [local, outside]},
            {"content": "手写"},
        ],
    )
    assert c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "isolated"}).status_code == 200
    current = c.get("/v1/memory", headers=me).json()
    assert [e["project_id"] for e in current["entries"]] == [pid, None, None]
    assert current["revision"] == doc["revision"] + 1
    with SessionLocal() as db:
        assert not db.get(User, uid).settings["memory"].get("tombstones")
    assert c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "isolated"}).status_code == 200
    assert c.get("/v1/memory", headers=me).json()["revision"] == current["revision"]


@pytest.mark.parametrize("failure", [False, True])
def test_merge_outcomes_and_failure_atomicity(api, monkeypatch, failure):
    c, me, uid = api
    pid = project(c, me, "isolated")
    conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
    account_source = _seed(uid, "账号重复来源")
    project_source = _seed(uid, "项目重复来源", conversation_id=conv)
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    recent = datetime.now(UTC).isoformat()
    doc = put(
        c,
        me,
        [
            {"content": "账号旧", "valid_from": old},
            {"content": "账号重复", "evidence_run_ids": [account_source]},
            {"content": "项目新", "project_id": pid, "valid_from": recent},
            {"content": "项目重复", "project_id": pid, "evidence_run_ids": [project_source]},
            {"content": "通用偏好", "project_id": pid},
            {"content": "项目约定", "project_id": pid},
        ],
        auto_save=False,
    )
    ids = [e["id"] for e in doc["entries"]]
    decisions = [
        {"id": ids[2], "action": "contradicts", "account_id": ids[0]},
        {"id": ids[3], "action": "duplicate_of", "account_id": ids[1]},
        {"id": ids[4], "action": "to_account"},
        {"id": ids[5], "action": "keep_project"},
    ]
    scripted(monkeypatch, decisions=decisions[:-1] if failure else decisions)
    response = c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "shared"})
    assert response.status_code == 200, response.text
    current = c.get("/v1/memory", headers=me).json()
    with SessionLocal() as db:
        pending = db.get(Project, pid).memory_merge_requested_at
        run = db.scalar(select(MemoryDreamRun).where(MemoryDreamRun.user_id == uid))
        # a bad plan leaves this project's data and request as they were, without failing the rest of the job
        assert run.status == ("skipped" if failure else "applied")
        assert bool((run.report or {}).get("merge_errors")) == failure
    if failure:
        assert current["entries"] == doc["entries"] and current["revision"] == doc["revision"] and pending
        return
    entries = {e["id"]: e for e in current["entries"]}
    assert ids[3] not in entries
    assert set(entries[ids[1]]["evidence_run_ids"]) == {account_source, project_source}
    assert entries[ids[0]]["superseded_by"] == ids[2]
    assert entries[ids[2]]["project_id"] is None and entries[ids[4]]["project_id"] is None
    assert entries[ids[5]]["project_id"] == pid and pending is None
    assert c.post(f"/v1/memory/dreams/{run.id}/revert", headers=me).status_code == 200
    assert len(c.get("/v1/memory", headers=me).json()["entries"]) == 6
    assert c.get("/v1/memory/summary", headers=me).json()["stale"]


def test_sensitive_agent_inference_rejected_but_explicit_saved(api):
    c, me, _uid_value = api
    rid = _running(c, me)
    headers = attempt_headers(c, rid)
    body = {"action": "add", "content": "用户患有慢性病", "expected_revision": 0}
    assert c.post(f"/internal/runs/{rid}/memory", headers={**headers, "X-Delivery-Id": rid + ":sensitive"}, json=body).status_code == 422
    body["explicit"] = True
    assert c.post(f"/internal/runs/{rid}/memory", headers={**headers, "X-Delivery-Id": rid + ":explicit"}, json=body).status_code == 200
    assert model_view(c.get("/v1/memory", headers=me).json())["entries"]


def test_summary_ownership_delete_cascade_and_edit_disabled(api):
    c, me, uid = api
    pid = project(c, me)
    other = _user(c)
    for path in ("/v1/memory/summary", "/v1/memory/entries"):
        method = c.get if path.endswith("summary") else c.delete
        assert method(path, headers=other, params={"project_id": pid}).status_code == 404
    assert c.post("/v1/memory/summary/edit", headers=other, json={"project_id": pid, "instruction": "记住"}).status_code == 404
    with SessionLocal() as db:
        db.add(MemorySummary(user_id=uid, project_id=pid, text="旧摘要", source_digest="old"))
        db.commit()
    assert c.delete(f"/v1/projects/{pid}", headers=me).status_code == 204
    with SessionLocal() as db:
        assert summary_row(db, uid, pid) is None
    doc = c.get("/v1/memory", headers=me).json()
    c.put("/v1/memory", headers=me, json={"expected_revision": doc["revision"], "enabled": False, "entries": []})
    assert c.post("/v1/memory/summary/edit", headers=me, json={"instruction": "记住"}).status_code == 409


def test_edit_remove_keeps_deletion_lineage_fence_on_revert(api, monkeypatch):
    c, me, _uid_value = api
    doc = put(c, me, [{"content": "明确忘记的事实"}])
    scripted(monkeypatch, ops=[{"op": "remove", "id": doc["entries"][0]["id"]}])
    result = edit(c, me, "忘记那条事实")
    assert result["status"] == "applied"
    assert not c.get("/v1/memory", headers=me).json()["entries"]
    assert c.post(f"/v1/memory/dreams/{result['id']}/revert", headers=me).status_code == 200
    assert not c.get("/v1/memory", headers=me).json()["entries"]


def test_summary_generation_bounded_account_first(api, monkeypatch):
    c, me, uid = api
    pids = [c.post("/v1/projects", headers=me, json={"name": str(index)}).json()["id"] for index in range(7)]
    put(c, me, [{"content": "账号事实"}, *[{"content": f"项目 {pid}", "project_id": pid} for pid in pids]])
    scripted(monkeypatch)
    assert c.post("/v1/memory/dream/run", headers=me).status_code == 202
    with SessionLocal() as db:
        rows = list(db.scalars(select(MemorySummary).where(MemorySummary.user_id == uid)))
        assert len(rows) == 5 and any(row.project_id is None for row in rows)
    assert c.post("/v1/memory/dream/run", headers=me).status_code == 202
    assert c.post("/v1/memory/dream/run", headers=me).json()["status"] == "nothing"


@pytest.mark.parametrize("mode,expected_scope", [("shared", None), ("isolated", "project")])
def test_dream_extraction_scope_and_judge_candidates(api, monkeypatch, mode, expected_scope):
    c, me, uid = api
    pid = project(c, me, mode)
    conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
    _seed(uid, "我喜欢中文回答", conversation_id=conv, days_ago=0)
    put(c, me, [{"kind": "user", "content": "账号偏好"}, {"content": "项目约定", "project_id": pid}])
    monkeypatch.setattr(service.settings, "memory_dream_min_runs", 1)
    seen = []

    async def extract(episodes, active, user):
        return (
            [
                {
                    "text": "喜欢中文回答",
                    "kind": "user",
                    "entities": [],
                    "confidence": 0.95,
                    "source_quote": "我喜欢中文回答",
                    "evidence_run_ids": [episodes[0].run_id],
                    "evidence_episode_ids": [episodes[0].id],
                    "scope": "account",
                }
            ],
            {},
            "fake",
        )

    async def judge(facts, active, user, model):
        seen.extend(active)
        return {0: {"supported": True}}, {}

    scripted(monkeypatch)
    monkeypatch.setattr(service, "_extract", extract)
    monkeypatch.setattr(service, "_judge", judge)
    ds = c.get("/v1/memory", headers=me).json()["dream"]
    c.patch("/v1/memory/dream", headers=me, json={**ds, "enabled": False})
    response = c.post("/v1/memory/dream/run", headers=me)
    result = c.get(f"/v1/memory/dreams/{response.json()['id']}", headers=me).json()
    assert result["status"] == "applied", result
    stored = next(e for e in c.get("/v1/memory", headers=me).json()["entries"] if e["content"] == "喜欢中文回答")
    assert stored["project_id"] == (pid if expected_scope else None)
    assert any(e.get("project_id") is None for e in seen) == (mode == "shared")


def test_committed_merge_remains_revertible_when_later_extraction_fails(api, monkeypatch):
    c, me, uid = api
    pid = project(c, me, "isolated")
    doc = put(c, me, [{"content": "中文回答偏好", "project_id": pid}])
    _seed(uid, "一些待抽取的用户原话", days_ago=0)
    monkeypatch.setattr(service.settings, "memory_dream_min_runs", 1)
    scripted(monkeypatch, decisions=[{"id": doc["entries"][0]["id"], "action": "to_account"}])

    async def unavailable(*args):
        raise service.DreamError("抽取服务暂不可用")

    monkeypatch.setattr(service, "_extract", unavailable)
    c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "shared"})
    with SessionLocal() as db:
        # The committed merge starts a second batch for the still-pending conversations. PostgreSQL
        # need not return the original batch first when an unordered query matches both jobs.
        run = db.scalar(select(MemoryDreamRun).where(MemoryDreamRun.user_id == uid, MemoryDreamRun.batch_index == 1))
        assert run.status == "applied" and run.error == "抽取服务暂不可用"
        assert run.plan["merges"]
        retry = db.scalar(select(MemoryDreamRun).where(MemoryDreamRun.user_id == uid, MemoryDreamRun.batch_index == 2))
        assert retry.status == "failed" and retry.error == "抽取服务暂不可用"
        did = run.id
    assert c.post(f"/v1/memory/dreams/{did}/revert", headers=me).status_code == 200
    assert c.get("/v1/memory", headers=me).json()["entries"][0]["project_id"] == pid


@pytest.mark.parametrize("stage", ["extract", "judge"])
def test_failed_extraction_still_refreshes_stale_summary(api, monkeypatch, stage):
    c, me, uid = api
    put(c, me, [{"kind": "user", "content": "中文回答"}])
    _seed(uid, "一些待抽取的用户原话", days_ago=0)
    monkeypatch.setattr(service.settings, "memory_dream_min_runs", 1)
    calls = scripted(monkeypatch)

    async def extracted(episodes, active, user):
        return (
            [{"text": "用户偏好中文", "source_quote": "用户原话", "evidence_run_ids": [episodes[0].run_id]}],
            {},
            "test-fast",
        )

    async def unavailable(*args):
        raise service.DreamError("抽取服务暂不可用")

    if stage == "judge":
        monkeypatch.setattr(service, "_extract", extracted)
    monkeypatch.setattr(service, "_" + stage, unavailable)
    assert c.get("/v1/memory/summary", headers=me).json()["stale"]
    response = c.post("/v1/memory/dream/run", headers=me)
    result = c.get(f"/v1/memory/dreams/{response.json()['id']}", headers=me).json()
    assert result["status"] == "failed" and result["error"] == "抽取服务暂不可用"
    summary = c.get("/v1/memory/summary", headers=me).json()
    assert summary["text"] and summary["entries"] == 1 and not summary["stale"]
    assert calls == [SUMMARY_SYSTEM]
    with SessionLocal() as db:
        run = db.get(MemoryDreamRun, response.json()["id"])
        assert run.claimed_until is None and "_final_status" not in (run.report or {})


def test_recovered_edit_does_not_replay_operations_or_create_edit_chain(api, monkeypatch):
    c, me, uid = api
    doc = put(c, me, [{"content": "已经保存的偏好"}])
    _seed(uid, "仍有待处理对话", days_ago=0)
    with SessionLocal() as db:
        run = MemoryDreamRun(
            user_id=uid,
            local_date="2026-11-01",
            timezone="Asia/Shanghai",
            status="running",
            trigger="edit",
            instruction="记住偏好",
            before_memory={**doc, "revision": doc["revision"] - 1, "entries": []},
            after_memory=doc,
            report={"_final_status": "applied", "reply": "已保存。"},
            claimed_until=datetime.now(UTC) - timedelta(minutes=1),
            claim_token="old",
        )
        db.add(run)
        db.commit()
        did = run.id
    calls = scripted(monkeypatch, ops=[{"op": "add", "content": "不能重复执行"}])
    c.portal.call(service.process_dream_chain, did)
    with SessionLocal() as db:
        assert db.get(MemoryDreamRun, did).status == "applied"
        assert len(list(db.scalars(select(MemoryDreamRun.id).where(MemoryDreamRun.user_id == uid)))) == 1
    assert len(calls) == 1 and calls[0] != EDIT_SYSTEM
    assert len(c.get("/v1/memory", headers=me).json()["entries"]) == 1


def test_summary_failure_preserves_committed_edit_reply_and_undo(api, monkeypatch):
    c, me, _uid_value = api

    async def chat(messages, model, user):
        if messages[0]["content"] == EDIT_SYSTEM:
            return '{"ops":[{"op":"add","content":"中文回答"}],"reply":"已保存。"}', {}
        raise service.DreamError("摘要服务暂不可用")

    monkeypatch.setattr(service, "_chat", chat)
    result = edit(c, me)
    assert result["status"] == "applied" and result["report"]["reply"] == "已保存。"
    assert result["report"]["summary_error"]
    assert c.get("/v1/memory/summary", headers=me).json()["stale"]
    assert c.post(f"/v1/memory/dreams/{result['id']}/revert", headers=me).status_code == 200


def test_cached_candidates_respect_new_isolation_and_sensitive_guard_is_consumed(api, monkeypatch):
    from ailab_platform.models import MemoryWorkItem

    c, me, uid = api
    pid = project(c, me, "isolated")
    conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
    prompt = "我喜欢中文。假设某人患有慢性病。"
    rid = _seed(uid, prompt, conversation_id=conv, days_ago=0)
    with SessionLocal() as db:
        facts = [
            {
                "text": content,
                "kind": "memory",
                "entities": [],
                "confidence": 0.95,
                "source_quote": prompt,
                "project_id": None,
                "evidence_run_ids": [rid],
                "evidence_episode_ids": [],
            }
            for content in ("用户喜欢中文", "用户患有慢性病")
        ]
        db.add(MemoryWorkItem(run_id=rid, user_id=uid, state="pending", source_end=len(prompt), candidates=facts))
        db.commit()
    scripted(monkeypatch)

    async def judge(facts, active, user, model):
        return {0: {"supported": True}}, {}

    monkeypatch.setattr(service, "_judge", judge)
    response = c.post("/v1/memory/dream/run", headers=me)
    out = c.get(f"/v1/memory/dreams/{response.json()['id']}", headers=me).json()
    assert out["status"] == "applied", out
    assert any(item["outcome"] == "guard" for item in out["report"]["candidates"])
    entries = c.get("/v1/memory", headers=me).json()["entries"]
    assert len(entries) == 1 and entries[0]["project_id"] == pid
    with SessionLocal() as db:
        work = db.get(MemoryWorkItem, rid)
        assert work.state == "done" and work.candidates == []


def test_nightly_auto_save_off_does_not_claim_conversation_only_work(api, monkeypatch):
    c, me, uid = api
    put(c, me, [], auto_save=False)
    _seed(uid, "仍有未处理历史", days_ago=0)
    c.post("/v1/memory/dream/run", headers=me)  # materialize the empty summary
    monkeypatch.setattr(service, "_candidate_users", lambda db, now: [db.get(User, uid)])
    assert service.claim_due(datetime(2026, 11, 1, 19, 0, tzinfo=UTC)) == []


def test_blank_project_ids_are_rejected(api):
    c, me, _uid_value = api
    assert c.get("/v1/memory/summary", headers=me, params={"project_id": ""}).status_code == 422
    assert c.delete("/v1/memory/entries", headers=me, params={"project_id": ""}).status_code == 422
    assert c.post("/v1/memory/summary/edit", headers=me, json={"project_id": "", "instruction": "记住"}).status_code == 422


def test_merge_undated_legacy_fact_cannot_override_known_newer_account_fact(api, monkeypatch):
    c, me, uid = api
    pid = project(c, me, "isolated")
    doc = put(c, me, [{"content": "当前账号事实"}, {"content": "旧项目事实", "project_id": pid}], auto_save=False)
    with SessionLocal() as db:
        user = db.get(User, uid)
        memory = copy.deepcopy(user.settings["memory"])
        memory["entries"][1]["valid_from"] = None
        user.settings = {**user.settings, "memory": memory}
        db.commit()
    scripted(monkeypatch, decisions=[{"id": doc["entries"][1]["id"], "action": "contradicts", "account_id": doc["entries"][0]["id"]}])
    c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "shared"})
    entries = c.get("/v1/memory", headers=me).json()["entries"]
    assert not entries[0]["invalid_at"] and entries[1]["superseded_by"] == entries[0]["id"]


def test_stale_scope_scan_uses_two_queries_instead_of_one_per_project(api):
    from sqlalchemy import event

    from ailab_platform.memory_dream.summary import stale_scopes

    c, me, uid = api
    pids = [c.post("/v1/projects", headers=me, json={"name": f"scope-{index}"}).json()["id"] for index in range(8)]
    put(c, me, [{"content": f"项目 {pid}", "project_id": pid} for pid in pids])
    with SessionLocal() as db:
        user = db.get(User, uid)
        queries = []

        def capture(conn, cursor, sql, params, context, many):
            if sql.lstrip().upper().startswith("SELECT"):
                queries.append(sql)

        event.listen(db.get_bind(), "before_cursor_execute", capture)
        try:
            assert len(stale_scopes(db, user, limit=5)) == 5
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", capture)
        assert len(queries) == 2


def test_isolation_rehomes_existing_evidence_from_soft_deleted_conversation(api):
    from ailab_platform.models import Conversation

    c, me, uid = api
    pid = project(c, me)
    conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
    rid = _seed(uid, "既有软删除来源", conversation_id=conv)
    put(c, me, [{"content": "只属于本项目", "evidence_run_ids": [rid]}])
    with SessionLocal() as db:
        db.get(Conversation, conv).deleted_at = datetime.now(UTC)
        db.commit()
    response = c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "isolated"})
    assert response.status_code == 200, response.text
    assert c.get("/v1/memory", headers=me).json()["entries"][0]["project_id"] == pid


def test_edit_skips_malformed_ops_and_reports_what_changed(api, monkeypatch):
    c, me, _uid_value = api
    doc = put(c, me, [{"kind": "user", "content": "中文回答"}, {"content": "旧公司"}])
    ops = [
        {"op": "add", "kind": "nonsense", "content": "新的偏好"},  # unknown kind falls back to a plain fact
        {"op": "add", "content": ""},  # empty: skipped, not fatal
        {"op": "add", "content": "x" * 5000},  # over the entry limit: skipped
        "not an op",
        {"op": "remove", "id": doc["entries"][1]["id"]},
    ]
    scripted(monkeypatch, ops=ops)
    result = edit(c, me, "忘记旧公司，记住新的偏好")
    assert result["status"] == "applied", result
    assert result["report"]["changes"] == {"added": 1, "replaced": 0, "removed": 1}
    contents = [e["content"] for e in c.get("/v1/memory", headers=me).json()["entries"]]
    assert contents == ["中文回答", "新的偏好"]


def test_edit_over_capacity_fails_with_a_reason(api, monkeypatch):
    c, me, _uid_value = api
    put(c, me, [{"content": f"事实{i}" + "长" * 110} for i in range(100)])
    scripted(monkeypatch, ops=[{"op": "add", "content": "再多一条"}])
    result = edit(c, me)
    assert result["status"] == "failed" and "记忆已满" in result["error"]


def test_settings_patch_changes_switches_without_touching_facts(api):
    c, me, _uid_value = api
    doc = put(c, me, [{"kind": "user", "content": "中文回答"}])
    saved = c.patch("/v1/memory/settings", headers=me, json={"auto_save": False})
    assert saved.status_code == 200 and saved.json() == {"enabled": True, "auto_save": False, "reference_history": True}
    after = c.get("/v1/memory", headers=me).json()
    assert after["entries"] == doc["entries"] and after["auto_save"] is False and after["revision"] == doc["revision"] + 1
    assert c.patch("/v1/memory/settings", headers=me, json={"project_only": True}).status_code == 422


def test_summary_survives_turning_memory_off(api, monkeypatch):
    c, me, uid = api
    scripted(monkeypatch)
    put(c, me, [{"kind": "user", "content": "中文回答"}])
    asyncio.run(regenerate(uid, None))
    assert c.patch("/v1/memory/settings", headers=me, json={"enabled": False}).status_code == 200
    summary = c.get("/v1/memory/summary", headers=me).json()
    assert summary["text"] and summary["entries"] == 1 and not summary["stale"]


def test_mock_model_answers_memory_prompts():
    from ailab_platform.llm import _memory_answer

    assert _memory_answer("other", "{}") is None
    assert json.loads(_memory_answer(EXTRACT_SYSTEM + "\n附加规则", "[]")) == {"facts": []}
    assert json.loads(_memory_answer(JUDGE_SYSTEM + "\n附加规则", "[]")) == {"results": []}
    assert "- 中文回答" in _memory_answer(SUMMARY_SYSTEM, json.dumps({"scope": "账号", "facts": ["中文回答"]}))
    entries = [{"id": "mem_a", "kind": "user", "content": "喜欢蓝色主题"}]
    forget = json.loads(_memory_answer(EDIT_SYSTEM, json.dumps({"instruction": "忘记蓝色主题", "entries": entries})))
    assert forget["ops"] == [{"op": "remove", "id": "mem_a"}]
    remember = json.loads(_memory_answer(EDIT_SYSTEM, json.dumps({"instruction": "记住我在上海", "entries": []})))
    assert remember["ops"][0]["content"] == "我在上海"
    merged = json.loads(_memory_answer(MERGE_SYSTEM, json.dumps({"project": [{"id": "mem_p"}], "account": []})))
    assert merged["decisions"] == [{"id": "mem_p", "action": "to_account"}]


@pytest.mark.parametrize("trigger", ["edit", "manual", "extraction"])
def test_batch_is_terminal_before_summary_and_not_reclaimed(api, monkeypatch, trigger):
    c, me, uid = api
    put(c, me, [{"content": "已有事实"}], auto_save=trigger == "extraction")
    calls = scripted(monkeypatch, ops=[{"op": "add", "content": "新增事实"}])
    checked, extracts = [], []
    source = _seed(uid, "本次已经处理的对话", days_ago=0) if trigger == "extraction" else None
    monkeypatch.setattr(service.settings, "memory_dream_min_runs", 1)

    async def extract_once(*args):
        extracts.append(True)
        return [], {}, "test-fast"

    monkeypatch.setattr(service, "_extract", extract_once)

    async def crash(user_id, pid):
        def check():
            with SessionLocal() as db:
                run = db.scalar(select(MemoryDreamRun).where(MemoryDreamRun.user_id == uid))
                assert run.status == ("skipped" if trigger == "manual" else "applied")
                assert run.finished_at and service._aware(run.claimed_until) > datetime.now(UTC)
                if run.status == "applied":
                    assert service._last_success(db, uid).id == run.id
                if source:
                    assert run.cursor_run_id == source
                    assert not service._new_runs(db, uid, service._last_success(db, uid), datetime.now(UTC))
                return run.id

        did = await asyncio.to_thread(check)
        checked.append(did)
        await service.process_dream(did)  # a redelivery while the summary is awaiting cannot replay the batch
        raise service.DreamError("摘要不可用")

    monkeypatch.setattr("ailab_platform.memory_dream.summary.regenerate", crash)
    monkeypatch.setattr("ailab_platform.memory_dream.maintenance.regenerate", crash)
    if trigger == "edit":
        result = edit(c, me)
    else:
        response = c.post("/v1/memory/dream/run", headers=me)
        result = c.get(f"/v1/memory/dreams/{response.json()['id']}", headers=me).json()
    assert checked == [result["id"]]  # assertions in the summary callback cannot be swallowed as a model failure
    assert result["report"]["summary_error"]
    with SessionLocal() as db:
        run = db.get(MemoryDreamRun, result["id"])
        assert run.claimed_until is None
        if source:
            assert run.cursor_run_id == source
    c.portal.call(service.process_dream, result["id"])
    assert len(calls) == (1 if trigger == "edit" else 0)
    assert len(extracts) == (1 if source else 0)


def test_summary_does_not_need_claim_and_deleted_job_is_safe(api, monkeypatch):
    c, me, uid = api
    put(c, me, [{"content": "已有事实"}], auto_save=False)

    async def delete_job(user_id, pid):
        def remove():
            with SessionLocal() as db:
                run = db.scalar(select(MemoryDreamRun).where(MemoryDreamRun.user_id == uid))
                db.delete(run)
                db.commit()

        await asyncio.to_thread(remove)
        raise RuntimeError("job deleted while summary model was waiting")

    monkeypatch.setattr("ailab_platform.memory_dream.summary.regenerate", delete_job)
    monkeypatch.setattr("ailab_platform.memory_dream.maintenance.regenerate", delete_job)
    assert c.post("/v1/memory/dream/run", headers=me).status_code == 202


@pytest.mark.parametrize("change", ["settings", "other_scope", "own_scope"])
def test_edit_conflict_depends_on_scope_facts_only(api, monkeypatch, change):
    c, me, uid = api
    pid = project(c, me)
    put(c, me, [{"content": "账号事实"}, {"content": "项目事实", "project_id": pid}])

    def concurrent(system, user):
        if system != EDIT_SYSTEM:
            return
        with SessionLocal() as db:
            owner = db.get(User, uid)
            memory = copy.deepcopy(owner.settings["memory"])
            memory["revision"] += 1
            if change != "settings":
                memory["entries"][0 if change == "own_scope" else 1]["content"] = "竞争写入的新事实"
            else:
                memory["reference_history"] = False
            owner.settings = {**owner.settings, "memory": memory}
            db.commit()

    scripted(monkeypatch, ops=[{"op": "add", "content": "明确新增"}], before=concurrent)
    result = edit(c, me)
    assert result["status"] == ("failed" if change == "own_scope" else "applied")
    doc = c.get("/v1/memory", headers=me).json()
    assert any(e["content"] == "明确新增" for e in doc["entries"]) == (change != "own_scope")
    if change == "other_scope":
        assert doc["entries"][1]["content"] == "竞争写入的新事实"
    if change == "settings":
        assert not doc["reference_history"]


def test_requested_scope_first_and_empty_projects_not_stale(api, monkeypatch):
    from ailab_platform.memory_dream.summary import stale_scopes

    c, me, uid = api
    requested = project(c, me)
    other = [project(c, me) for _ in range(6)]
    empty = project(c, me)
    put(
        c,
        me,
        [{"content": "账号事实"}, *[{"content": f"项目事实 {pid}", "project_id": pid} for pid in [requested, *other]]],
        auto_save=False,
    )
    with SessionLocal() as db:
        scopes = stale_scopes(db, db.get(User, uid), limit=5, requested=requested)
        assert scopes[0] == requested and len(scopes) == 5
        assert empty not in stale_scopes(db, db.get(User, uid))
    assert not c.get("/v1/memory/summary", headers=me, params={"project_id": empty}).json()["stale"]
    calls = scripted(monkeypatch)
    assert c.post("/v1/memory/dream/run", headers=me, params={"project_id": requested}).status_code == 202
    assert len(calls) == 5
    assert not c.get("/v1/memory/summary", headers=me, params={"project_id": requested}).json()["stale"]
    assert c.post("/v1/memory/dream/run", headers=me, params={"project_id": "missing"}).status_code == 404


def test_summary_get_never_scans_history(api, monkeypatch):
    c, me, _ = api

    def forbidden(*args, **kwargs):
        pytest.fail("summary GET must not scan historical runs")

    monkeypatch.setattr(service, "_new_runs", forbidden)
    value = c.get("/v1/memory/summary", headers=me).json()
    assert value["enabled"] and not value["stale"] and "pending_runs" not in value


def test_redaction_only_writes_changed_rows_and_preserves_active_edit(api):
    from sqlalchemy import event

    from ailab_platform.memory_recall.lineage import redact_memory_copies

    _, _, uid = api
    with SessionLocal() as db:
        runs = [
            MemoryDreamRun(user_id=uid, local_date="2026-11-01", timezone="UTC", trigger="edit", status=status, instruction="用户的新指令")
            for status in ("pending", "applied")
        ]
        db.add_all(runs)
        db.commit()
        ids = [r.id for r in runs]
        updates = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE memory_dream_runs"):
                updates.append(statement)

        event.listen(db.bind, "before_cursor_execute", capture)
        try:
            redact_memory_copies(db, uid, [{"id": "mem_removed", "content": "已忘记的事实"}])
            db.commit()
            assert len(updates) == 1  # only the completed instruction is a retained copy to clear
            assert db.get(MemoryDreamRun, ids[0]).instruction == "用户的新指令"
            updates.clear()
            redact_memory_copies(db, uid, [{"id": "mem_removed", "content": "已忘记的事实"}])
            db.commit()
            assert not updates
        finally:
            event.remove(db.bind, "before_cursor_execute", capture)


def test_history_pool_modes_resolved_once(api):
    from sqlalchemy import event

    from ailab_platform.memory_recall.policy import history_pool_reader
    from ailab_platform.models import Run

    c, me, uid = api
    shared, isolated = project(c, me), project(c, me, "isolated")
    sources = []
    for pid in (None, shared, isolated):
        conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
        sources.append(_seed(uid, "可引用的历史", conversation_id=conv))
    with SessionLocal() as db:
        queries = []

        def capture(conn, cursor, statement, parameters, context, executemany):
            if "FROM projects" in statement:
                queries.append(statement)

        event.listen(db.bind, "before_cursor_execute", capture)
        try:
            allowed = history_pool_reader(db, db.get(Run, sources[0]))
            for _ in range(4):
                assert [allowed(db.get(Run, rid)) for rid in sources] == [True, True, False]
            assert len(queries) == 1
        finally:
            event.remove(db.bind, "before_cursor_execute", capture)


def test_terminal_summary_calls_are_metered_without_reopening_batch(api, monkeypatch):
    from ailab_platform.llm import LLMResponse
    from ailab_platform.models import ModelUsage, UsageReservation

    c, me, uid = api
    put(c, me, [{"content": "已有事实"}], auto_save=False)

    class Model:
        async def chat(self, messages, tools, model, **kwargs):
            def check():
                with SessionLocal() as db:
                    run = db.scalar(select(MemoryDreamRun).where(MemoryDreamRun.user_id == uid))
                    assert run.status == "skipped" and service._aware(run.claimed_until) > datetime.now(UTC)

            await asyncio.to_thread(check)
            return LLMResponse(content="你有已有事实。", usage={"prompt_tokens": 20, "completion_tokens": 5})

    monkeypatch.setattr(service, "llm_for", lambda model: Model())
    response = c.post("/v1/memory/dream/run", headers=me)
    with SessionLocal() as db:
        usage = db.scalar(select(ModelUsage).where(ModelUsage.job_id == response.json()["id"]))
        assert usage and usage.mode == "summary" and usage.status == "settled"
        assert db.get(UsageReservation, usage.call_id).status == "settled"
    assert not c.get("/v1/memory/summary", headers=me).json()["stale"]


@pytest.mark.parametrize("trigger", ["manual", "edit"])
def test_summary_phase_keeps_job_active_until_new_text_is_visible(api, monkeypatch, trigger):
    from threading import Event

    c, me, uid = api
    put(c, me, [{"content": "原有事实"}], auto_save=False)
    scripted(monkeypatch, ops=[{"op": "add", "content": "新增事实"}])
    entered, release = Event(), Event()
    original = regenerate

    async def blocked(user_id, pid):
        entered.set()
        assert await asyncio.to_thread(release.wait, 20)
        await original(user_id, pid)

    monkeypatch.setattr("ailab_platform.memory_dream.summary.regenerate", blocked)
    monkeypatch.setattr("ailab_platform.memory_dream.maintenance.regenerate", blocked)
    with SessionLocal() as db:
        run = MemoryDreamRun(user_id=uid, local_date="2026-11-01", timezone="UTC", trigger=trigger, instruction="记住新增事实")
        db.add(run)
        db.add(MemorySummary(user_id=uid, text="旧摘要", source_digest="obsolete"))
        db.commit()
        did = run.id
    future = c.portal.start_task_soon(service.process_dream, did)
    try:
        assert entered.wait(20)
        summary = c.get("/v1/memory/summary", headers=me).json()
        assert summary["active"]["id"] == did
        assert summary["text"] == "旧摘要"
        assert summary["active"]["status"] in {"applied", "skipped"}
        assert c.post("/v1/memory/summary/edit", headers=me, json={"instruction": "不能并发"}).status_code == 409
        reused = c.post("/v1/memory/dream/run", headers=me).json()
        assert reused["reused"] and reused["id"] == did
        assert c.get("/v1/memory/dream/status", headers=me).json()["active"]["id"] == did
    finally:
        release.set()
        future.result(timeout=25)
    summary = c.get("/v1/memory/summary", headers=me).json()
    assert summary["active"] is None and summary["text"] != "旧摘要" and not summary["stale"]


def test_expired_final_summary_lease_is_inactive_but_never_reclaimed(api):
    c, me, uid = api
    with SessionLocal() as db:
        run = MemoryDreamRun(
            user_id=uid,
            local_date="2026-11-01",
            timezone="UTC",
            status="applied",
            claimed_until=datetime.now(UTC) - timedelta(seconds=1),
            cursor_run_id="completed-source",
        )
        db.add(run)
        db.commit()
        did = run.id
    assert c.get("/v1/memory/summary", headers=me).json()["active"] is None
    c.portal.call(service.process_dream, did)
    with SessionLocal() as db:
        run = db.get(MemoryDreamRun, did)
        assert run.status == "applied" and run.cursor_run_id == "completed-source"
