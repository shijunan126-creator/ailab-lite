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
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.memory_dream.changes import digest
from ailab_platform.memory_dream.prompts import DOCUMENT_SYSTEM, EDIT_SYSTEM, MERGE_SYSTEM
from ailab_platform.memory_recall.policy import model_view
from ailab_platform.models import MemoryDreamRun, Project, User
from tests.memory_fixtures import make_dream, snapshot
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
    response = post_edit(c, me, {"project_id": pid, "instruction": instruction})
    assert response.status_code == 202, response.text
    return c.get(f"/v1/memory/dreams/{response.json()['id']}", headers=me).json()


@pytest.mark.parametrize("mode,account", [(None, True), ("shared", True), ("isolated", False)])
def test_visibility_matrix_bootstrap_and_live_tools(api, mode, account):
    c, me, _uid_value = api
    pid = project(c, me, mode or "shared") if mode else None
    conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
    entries = [{"kind": "user", "content": "中文回答", "id": "mem_account" + _uid_value[4:20]}]
    if pid:
        entries.append({"kind": "user", "content": "中文项目", "project_id": pid, "id": "mem_project" + _uid_value[4:20]})
    put(c, me, entries)
    rid = c.post(f"/v1/conversations/{conv}/messages", headers=me, json={"prompt": "中文"}).json()["id"]
    expected = ({"mem_account" + _uid_value[4:20]} if account else set()) | ({"mem_project" + _uid_value[4:20]} if pid else set())
    assert {e["id"] for e in c.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()["memory"]["entries"]} == expected
    headers = attempt_headers(c, rid)
    c.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"})
    for action in ("list",):
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
    headers = attempt_headers(c, rid)
    c.post(f"/internal/runs/{rid}/transition", headers=headers, json={"status": "running"})
    out = c.post(f"/internal/runs/{rid}/memory", headers=headers, json={"action": "search", "query": "BLUEFIN"}).json()
    allowed = {sources[isolated]} if mode == "isolated" else {sources[None], sources[shared]}
    assert {e["run_id"] for e in out["results"]} == allowed
    for source in sources.values():
        assert c.post(f"/internal/runs/{rid}/recall", headers=SERVICE, json={"run_id": source}).status_code == (
            200 if source in allowed else 404
        )


def test_pages_are_derived_without_model_calls_when_auto_save_off(api, monkeypatch):
    c, me, _ = api
    calls = scripted(monkeypatch)
    assert c.get("/v1/memory/view", headers=me).json()["pages"] == []
    put(c, me, [{"kind": "user", "content": "中文回答"}], auto_save=False)
    assert c.post("/v1/memory/dream/run", headers=me).json()["status"] == "nothing"
    assert c.get("/v1/memory/view", headers=me).json()["core"][0]["content"] == "中文回答"
    assert not calls


def test_edit_replace_revert_restores_item_and_scope(api, monkeypatch):
    c, me, _ = api
    pid = project(c, me, "isolated")
    doc = put(c, me, [{"kind": "user", "content": "中文回答"}, {"content": "项目旧偏好", "project_id": pid}])
    target = doc["entries"][1]["id"]
    calls = scripted(
        monkeypatch, ops=[{"op": "replace", "id": target, "content": "项目新偏好"}, {"op": "remove", "id": doc["entries"][0]["id"]}]
    )
    result = edit(c, me, pid=pid)
    assert result["status"] == "applied"
    assert len(calls) == 1
    assert c.get("/v1/memory/items", headers=me, params={"project_id": pid}).json()["items"][0]["content"] == "项目新偏好"
    assert c.post(f"/v1/memory/dreams/{result['id']}/revert", headers=me).json()["status"] == "reverted"
    assert c.get("/v1/memory/items", headers=me, params={"project_id": pid}).json()["items"][0]["content"] == "项目旧偏好"
    assert c.get("/v1/memory", headers=me).json()["entries"][0]["content"] == "中文回答"


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
                memory = memory_for(owner)
                memory["entries"] = [{**e, "content": "竞争修改事实"} for e in memory["entries"]]
                from ailab_platform.memory_recall.store import replace_entries

                replace_entries(db, owner, memory["entries"])
                owner.settings = {
                    **owner.settings,
                    "memory": {**{k: v for k, v in memory.items() if k != "entries"}, "revision": memory["revision"] + 1},
                }
                db.commit()

    scripted(monkeypatch, ops=[{"op": "add", "content": "不会写入"}], before=change)
    conflict = edit(c, me)
    assert conflict["status"] == "failed" and conflict["error"] == "记忆已变化，请重试"
    with SessionLocal() as db:
        db.add(MemoryDreamRun(user_id=uid, local_date="2026-11-01", timezone="Asia/Shanghai", status="pending", trigger="manual"))
        db.commit()
    response = post_edit(c, me, {"instruction": "忘记"})
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
    assert response.status_code == 200 and response.json()["pages"] == []
    assert [e["content"] for e in c.get("/v1/memory", headers=me).json()["entries"]] == ["账号保留"]
    with SessionLocal() as db:
        assert len(db.get(User, uid).settings["memory"]["tombstones"]) == 3
    assert c.delete("/v1/memory/entries", headers=me).json()["pages"] == []


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
    by_content = {e["content"]: e["id"] for e in doc["entries"]}
    ids = [by_content[c] for c in ("账号旧", "账号重复", "项目新", "项目重复", "通用偏好", "项目约定")]
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
    assert c.get("/v1/memory/view", headers=me).status_code == 200


def test_sensitive_agent_inference_rejected_but_explicit_saved(api):
    c, me, _uid_value = api
    rid = _running(c, me)
    headers = attempt_headers(c, rid)
    body = {"action": "add", "content": "用户患有慢性病", "expected_revision": 0}
    assert c.post(f"/internal/runs/{rid}/memory", headers={**headers, "X-Delivery-Id": rid + ":sensitive"}, json=body).status_code == 422
    body["explicit"] = True
    assert c.post(f"/internal/runs/{rid}/memory", headers={**headers, "X-Delivery-Id": rid + ":explicit"}, json=body).status_code == 200
    assert model_view(c.get("/v1/memory", headers=me).json())["entries"]


def test_items_ownership_delete_cascade_and_edit_disabled(api):
    c, me, _ = api
    pid = project(c, me)
    other = _user(c)
    for path in ("/v1/memory/view", "/v1/memory/items"):
        assert c.get(path, headers=other, params={"project_id": pid}).status_code == 404
    assert (
        c.post("/v1/memory/edit", headers=other, json={"project_id": pid, "instruction": "记住", "expected_revision": 0}).status_code == 404
    )
    put(c, me, [{"content": "项目事实", "project_id": pid}])
    assert c.delete(f"/v1/projects/{pid}", headers=me).status_code == 204
    assert not c.get("/v1/memory", headers=me).json()["entries"]
    c.patch("/v1/memory/settings", headers=me, json={"enabled": False})
    assert post_edit(c, me, {"instruction": "记住"}).status_code == 409


def test_edit_remove_keeps_deletion_lineage_fence_on_revert(api, monkeypatch):
    c, me, _uid_value = api
    doc = put(c, me, [{"content": "明确忘记的事实"}])
    scripted(monkeypatch, ops=[{"op": "remove", "id": doc["entries"][0]["id"]}])
    result = edit(c, me, "忘记那条事实")
    assert result["status"] == "applied"
    assert not c.get("/v1/memory", headers=me).json()["entries"]
    assert c.post(f"/v1/memory/dreams/{result['id']}/revert", headers=me).status_code == 409
    assert not c.get("/v1/memory", headers=me).json()["entries"]


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
    response = c.post("/v1/memory/dream/run", headers=me, params={"project_id": pid})
    result = c.get(f"/v1/memory/dreams/{response.json()['id']}", headers=me).json()
    assert result["status"] == "applied", result
    stored = next(e for e in c.get("/v1/memory", headers=me).json()["entries"] if e["content"] == "喜欢中文回答")
    assert stored["project_id"] == (pid if expected_scope else None)
    assert any(e.get("project_id") is None for e in seen) == (mode == "shared")


def test_committed_merge_remains_revertible_when_later_extraction_fails(api, monkeypatch):
    c, me, uid = api
    pid = project(c, me, "isolated")
    doc = put(c, me, [{"content": "中文回答偏好", "project_id": pid}])
    conv = c.post("/v1/conversations", headers=me, json={"project_id": pid}).json()["id"]
    _seed(uid, "一些待抽取的用户原话", days_ago=0, conversation_id=conv)
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
    scripted(monkeypatch)

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
    assert c.get("/v1/memory/view", headers=me).status_code == 200
    response = c.post("/v1/memory/dream/run", headers=me)
    result = c.get(f"/v1/memory/dreams/{response.json()['id']}", headers=me).json()
    assert result["status"] == "failed" and result["error"] == "抽取服务暂不可用"
    view = c.get("/v1/memory/view", headers=me).json()
    assert view["core"][0]["content"] == "中文回答"
    with SessionLocal() as db:
        run = db.get(MemoryDreamRun, response.json()["id"])
        assert run.claimed_until is None and "_final_status" not in (run.report or {})


def test_recovered_edit_does_not_replay_operations_or_create_edit_chain(api, monkeypatch):
    c, me, uid = api
    doc = put(c, me, [{"content": "已经保存的偏好"}])
    _seed(uid, "仍有待处理对话", days_ago=0)
    with SessionLocal() as db:
        run = make_dream(
            user_id=uid,
            local_date="2026-11-01",
            timezone="Asia/Shanghai",
            status="applied",
            trigger="edit",
            instruction="记住偏好",
            before_memory={**doc, "revision": doc["revision"] - 1, "entries": []},
            after_memory=doc,
            report={"stage_committed": True, "reply": "已保存。"},
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
    assert calls == []
    assert len(c.get("/v1/memory", headers=me).json()["entries"]) == 1


def test_edit_needs_no_summary_call_and_keeps_undo(api, monkeypatch):
    c, me, _ = api
    calls = scripted(monkeypatch, ops=[{"op": "add", "content": "中文回答"}])
    result = edit(c, me)
    assert result["status"] == "applied" and result["report"]["reply"]
    assert calls == [EDIT_SYSTEM]
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
        return {0: {"supported": True}, 1: {"supported": False}}, {}

    monkeypatch.setattr(service, "_judge", judge)
    response = c.post("/v1/memory/dream/run", headers=me, params={"project_id": pid})
    out = c.get(f"/v1/memory/dreams/{response.json()['id']}", headers=me).json()
    assert out["status"] == "applied", out
    assert any(item["outcome"] == "unsupported" for item in out["report"]["candidates"])
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
    assert c.get("/v1/memory/view", headers=me, params={"project_id": ""}).status_code == 422
    assert c.delete("/v1/memory/entries", headers=me, params={"project_id": ""}).status_code == 422
    assert post_edit(c, me, {"project_id": "", "instruction": "记住"}).status_code == 422


def test_merge_undated_legacy_fact_cannot_override_known_newer_account_fact(api, monkeypatch):
    c, me, _uid = api
    pid = project(c, me, "isolated")
    doc = put(c, me, [{"content": "当前账号事实"}, {"content": "旧项目事实", "project_id": pid}], auto_save=False)
    with SessionLocal() as db:
        from ailab_platform.models import MemoryItem

        db.get(MemoryItem, doc["entries"][1]["id"]).valid_from = datetime(1970, 1, 1, tzinfo=UTC)
        db.commit()
    scripted(monkeypatch, decisions=[{"id": doc["entries"][1]["id"], "action": "contradicts", "account_id": doc["entries"][0]["id"]}])
    c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "shared"})
    entries = c.get("/v1/memory", headers=me).json()["entries"]
    assert not entries[0]["invalid_at"] and entries[1]["superseded_by"] == entries[0]["id"]


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
    put(c, me, [{"content": f"事实{i}"} for i in range(2000)])
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


def test_pages_survive_turning_memory_off(api, monkeypatch):
    c, me, _ = api
    put(c, me, [{"kind": "user", "content": "中文回答"}])
    c.patch("/v1/memory/settings", headers=me, json={"enabled": False})
    view = c.get("/v1/memory/view", headers=me).json()
    assert not view["enabled"] and view["core"][0]["content"] == "中文回答"


def test_mock_model_answers_memory_prompts():
    from ailab_platform.llm import _memory_answer

    assert _memory_answer("other", "{}") is None
    assert json.loads(_memory_answer(DOCUMENT_SYSTEM + "\n附加规则", "[]")) == {"facts": []}
    assert json.loads(_memory_answer(DOCUMENT_SYSTEM + "\n附加规则", "[]")) == {"results": []}
    entries = [{"id": "mem_a", "kind": "user", "content": "喜欢蓝色主题"}]
    forget = json.loads(_memory_answer(EDIT_SYSTEM, json.dumps({"instruction": "忘记蓝色主题", "entries": entries})))
    assert forget["ops"] == [{"op": "remove", "id": "mem_a"}]
    remember = json.loads(_memory_answer(EDIT_SYSTEM, json.dumps({"instruction": "记住我在上海", "entries": []})))
    assert remember["ops"][0]["content"] == "我在上海"
    merged = json.loads(_memory_answer(MERGE_SYSTEM, json.dumps({"project": [{"id": "mem_p"}], "account": []})))
    assert merged["decisions"] == [{"id": "mem_p", "action": "to_account"}]


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
            memory = copy.deepcopy(memory_for(owner))
            memory["revision"] += 1
            if change != "settings":
                memory["entries"][0 if change == "own_scope" else 1]["content"] = "竞争写入的新事实"
            else:
                memory["reference_history"] = False
            from ailab_platform.memory_recall.store import replace_entries

            replace_entries(db, owner, memory.pop("entries"))
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
    if change != "own_scope":
        assert c.post(f"/v1/memory/dreams/{result['id']}/revert", headers=me).status_code == 200
        restored = c.get("/v1/memory", headers=me).json()
        assert not any(e["content"] == "明确新增" for e in restored["entries"])
        if change == "other_scope":
            assert restored["entries"][1]["content"] == "竞争写入的新事实"
        else:
            assert not restored["reference_history"]


def test_view_get_never_scans_history(api, monkeypatch):
    c, me, _ = api

    def forbidden(*args, **kwargs):
        pytest.fail("view GET must not scan historical runs")

    monkeypatch.setattr(service, "_new_runs", forbidden)
    value = c.get("/v1/memory/view", headers=me).json()
    assert value["enabled"] and value["pages"] == []


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
    assert c.get("/v1/memory/view", headers=me).json()["active"] is None
    c.portal.call(service.process_dream, did)
    with SessionLocal() as db:
        run = db.get(MemoryDreamRun, did)
        assert run.status == "applied" and run.cursor_run_id == "completed-source"


def test_shared_project_editor_rejected_at_request_and_commit(api, monkeypatch):
    c, me, _uid = api
    pid = project(c, me)
    response = post_edit(c, me, {"project_id": pid, "instruction": "记住"})
    assert response.status_code == 409
    c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "isolated"})

    def switch(system, user):
        if system == EDIT_SYSTEM:
            with SessionLocal() as db:
                db.get(Project, pid).memory_mode = "shared"
                db.commit()

    scripted(monkeypatch, ops=[{"op": "add", "content": "迟到写入"}], before=switch)
    result = edit(c, me, pid=pid)
    assert result["status"] == "failed" and "共享" in result["error"]
    assert not c.get("/v1/memory", headers=me).json()["entries"]


@pytest.mark.parametrize("operation", ["clear", "delete_project"])
def test_empty_scope_clear_fences_queued_edits_and_old_snapshots(api, monkeypatch, operation):
    c, me, uid = api
    pid = project(c, me, "isolated") if operation == "delete_project" else None
    # The current scope is empty, but historical copies and a queued explicit add still exist.
    old = {"id": "mem_old", "kind": "memory", "content": "旧事实", "project_id": pid}
    with SessionLocal() as db:
        queued = make_dream(
            user_id=uid,
            project_id=pid,
            local_date="2026-11-02",
            timezone="UTC",
            trigger="edit",
            status="pending",
            instruction="记住旧事实",
            before_memory={"entries": []},
        )
        past = make_dream(
            user_id=uid,
            project_id=pid,
            local_date="2026-11-01",
            timezone="UTC",
            trigger="edit",
            status="applied",
            before_memory={"entries": [old]},
            report={"summary_before": {"text": "旧事实"}},
        )
        db.add_all([queued, past])
        db.commit()
        queued_id, past_id = queued.id, past.id
    if operation == "clear":
        assert c.delete("/v1/memory/entries", headers=me).status_code == 200
    else:
        assert c.delete(f"/v1/projects/{pid}", headers=me).status_code == 204
    calls = scripted(monkeypatch, ops=[{"op": "add", "content": "旧事实"}])
    asyncio.run(service.process_dream(queued_id))
    with SessionLocal() as db:
        assert db.get(MemoryDreamRun, queued_id).status == "failed"
        assert db.get(MemoryDreamRun, queued_id).instruction == ""
        assert snapshot(db.get(MemoryDreamRun, past_id), "before")["entries"] == []
        assert "summary_before" not in db.get(MemoryDreamRun, past_id).report
    assert calls == []
    assert not c.get("/v1/memory", headers=me).json()["entries"]


def test_forgetting_one_scope_preserves_same_text_in_other_scope_snapshots(api):
    c, me, uid = api
    pid = project(c, me)
    doc = put(c, me, [{"content": "同一偏好"}, {"content": "同一偏好", "project_id": pid}])
    with SessionLocal() as db:
        old = make_dream(user_id=uid, local_date="2026-11-01", timezone="UTC", status="applied", before_memory=doc, after_memory=doc)
        db.add(old)
        db.commit()
        old_id = old.id
    c.delete("/v1/memory/entries", headers=me)
    with SessionLocal() as db:
        for field in ("before_memory", "after_memory"):
            entries = snapshot(db.get(MemoryDreamRun, old_id), field.split("_")[0])["entries"]
            assert [e["project_id"] for e in entries] == [pid]
    assert [e["project_id"] for e in c.get("/v1/memory", headers=me).json()["entries"]] == [pid]


def test_queued_edit_digest_survives_fact_redaction(api, monkeypatch):
    c, me, uid = api
    doc = put(c, me, [{"content": "待忘记的事实"}])
    with SessionLocal() as db:
        queued = make_dream(
            user_id=uid,
            local_date="2026-11-02",
            timezone="UTC",
            trigger="edit",
            status="pending",
            instruction="改成另一个事实",
            before_memory=doc,
            report={"edit_source_digest": digest(doc["entries"])},
        )
        db.add(queued)
        db.commit()
        queued_id = queued.id
    # An ordinary deletion scrubs the queued job's textual snapshot as well.
    put(c, me, [])
    calls = scripted(monkeypatch, ops=[{"op": "add", "content": "不应重建"}])
    asyncio.run(service.process_dream(queued_id))
    with SessionLocal() as db:
        assert db.get(MemoryDreamRun, queued_id).status == "failed"
    assert not calls
    assert not c.get("/v1/memory", headers=me).json()["entries"]


def test_clear_during_model_wait_cancels_explicit_add(api, monkeypatch):
    c, me, _ = api

    def clear(system, user):
        if system == EDIT_SYSTEM:
            assert c.delete("/v1/memory/entries", headers=me).status_code == 200

    scripted(monkeypatch, ops=[{"op": "add", "content": "清空前排队的新增"}], before=clear)
    result = edit(c, me)
    assert result["status"] == "failed"
    assert not c.get("/v1/memory", headers=me).json()["entries"]


def test_mode_roundtrip_does_not_replay_a_queued_isolated_edit(api, monkeypatch):
    c, me, uid = api
    pid = project(c, me, "isolated")
    with SessionLocal() as db:
        queued = make_dream(
            user_id=uid,
            project_id=pid,
            local_date="2026-11-02",
            timezone="UTC",
            trigger="edit",
            status="pending",
            instruction="记住旧的项目要求",
            before_memory={"entries": []},
        )
        db.add(queued)
        db.commit()
        queued_id = queued.id
    calls = scripted(monkeypatch, ops=[{"op": "add", "content": "不应恢复的指令"}])
    assert c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "shared"}).status_code == 200
    assert c.patch(f"/v1/projects/{pid}", headers=me, json={"memory_mode": "isolated"}).status_code == 200
    calls.clear()
    asyncio.run(service.process_dream(queued_id))
    with SessionLocal() as db:
        assert db.get(MemoryDreamRun, queued_id).status == "failed"
    assert calls == []
    assert not c.get("/v1/memory", headers=me).json()["entries"]


def post_edit(c, headers, body):
    return c.post(
        "/v1/memory/edit", headers=headers, json={"expected_revision": c.get("/v1/memory", headers=headers).json()["revision"], **body}
    )
