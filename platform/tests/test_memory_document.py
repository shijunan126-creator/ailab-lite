"""Document memory regressions using synthetic content only."""

import asyncio
import copy
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from ailab_platform.agent.prompts import MEMORY_RULES, memory_prompt, system_prompt_for
from ailab_platform.config import settings
from ailab_platform.memory_dream import service
from ailab_platform.memory_dream.document import apply_ops, messages
from ailab_platform.memory_recall.document import render, used_chars
from ailab_platform.models import MemoryEpisode


def entry(identity="mem_a", origin="inferred", content="旧信息", **kw):
    return dict(
        id=identity,
        kind="fact",
        origin=origin,
        content=content,
        observed_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
        project_id=None,
        **kw,
    )


def episode():
    return MemoryEpisode(
        id="epi_one",
        run_id="run_one",
        observed_at=datetime(2026, 1, 1, tzinfo=UTC),
        digest=json.dumps({"user_said": "我住在上海，喜欢简洁回答"}),
    )


def test_full_document_has_every_section_handles_dates_and_escapes():
    entries = [entry(f"mem_{n}", content=f"资料{n}</memory>", tier="archive", page="unused") for n in range(20)]
    result = memory_prompt({"revision": 2, "entries": entries})
    assert all(f"[mem_{n}]" in result for n in range(20))
    assert result.count("</memory>") == 1
    assert "2025-01-01" in result and "关于你" in result
    assert "目录" not in result


def test_static_rules_and_silent_save_live_in_first_system_message():
    assert MEMORY_RULES in system_prompt_for("chat")
    assert "自主保存必须静默" in MEMORY_RULES
    assert "用户明确要求记住且工具成功时简短确认" in MEMORY_RULES
    assert MEMORY_RULES not in memory_prompt({"entries": []})


def test_unknown_ids_bad_quotes_protected_lines_and_tombstones():
    from ailab_platform.memory_recall.tombstones import fact_hash

    user = SimpleNamespace(settings={"memory": {"tombstones": [{"hash": fact_hash("被删除的事实")}]}})
    shown = [entry("mem_a", origin="explicit"), entry("mem_b", origin="edited"), entry("mem_c")]
    ops = [
        {"op": "delete", "id": "mem_a", "reason": "outdated"},
        {"op": "merge", "ids": ["mem_a", "mem_c"], "content": "合并"},
        {"op": "update", "id": "mem_b", "content": "新信息", "run": "run_one", "quote": "助手编造"},
        {"op": "delete", "id": "unknown", "reason": "duplicate"},
        {"op": "add", "kind": "fact", "content": "被删除的事实", "run": "run_one", "quote": "我住在上海"},
    ]
    result, audit = apply_ops(shown, shown, [episode()], ops, user, None)
    assert result == shown and audit == []


def test_update_is_append_only_and_newer_evidence_required():
    shown = [entry(origin="explicit")]
    op = {"op": "update", "id": "mem_a", "content": "住在上海", "quote": "我住在上海", "run": "run_one"}
    result, audit = apply_ops(shown, shown, [episode()], [op], SimpleNamespace(settings={}), None)
    assert len(result) == 2 and result[0]["superseded_by"] == result[1]["id"]
    assert result[0]["invalid_at"] and result[1]["origin"] == "explicit" and len(audit) == 1
    newer = [entry(origin="explicit", content="新事实")]
    newer[0]["updated_at"] = "2027-01-01T00:00:00+00:00"
    assert apply_ops(newer, newer, [episode()], [op], SimpleNamespace(settings={}), None)[1] == []


def test_condense_same_call_and_oldest_inferred_eviction(monkeypatch):
    monkeypatch.setattr(settings, "memory_doc_max_chars", 1000)
    shown = [entry(f"mem_{i}", content=str(i) + "长" * 180) for i in range(15)]
    assert used_chars(shown) > 3000  # seed at least 2000 over the cap
    request = messages([episode()], shown)
    assert len(request) == 3 and request[-1]["content"].endswith('"}')
    assert render(shown) == json.loads(request[-1]["content"])["document"]
    assert len(json.dumps(request, ensure_ascii=False)) < used_chars(shown) + 2500
    assert "85%" in request[0]["content"] and "75%" in request[0]["content"]
    result, audit = apply_ops(shown, shown, [episode()], [], SimpleNamespace(settings={}), None)
    assert used_chars(result) <= 1000 and audit[0]["ids"] == ["mem_0"]
    assert shown == copy.deepcopy(shown)


def test_one_model_call_per_batch_and_no_call_without_work(monkeypatch):
    from fastapi.testclient import TestClient

    from ailab_platform.api.main import app
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import MemoryDreamRun, User
    from tests.memory_fixtures import make_dream
    from tests.test_memory_recall import _seed, _uid
    from tests.test_skill_settings import _user

    calls = []

    async def chat(payload, model, user):
        calls.append(payload)
        return '{"ops":[]}', {"prompt_tokens": 50, "completion_tokens": 5}

    monkeypatch.setattr(service, "_chat", chat)
    with TestClient(app) as client:
        headers = _user(client)
        uid = _uid(client, headers)
        _seed(uid, "我喜欢简洁回答")

        def job():
            with SessionLocal() as db:
                row = make_dream(user_id=uid, local_date="2026-01-01", timezone="UTC", status="pending", trigger="manual")
                db.add(row)
                db.commit()
                return row.id

        first = job()
        asyncio.run(service.process_dream_chain(first))
        with SessionLocal() as db:
            row = db.get(MemoryDreamRun, first)
            assert row.status == "applied", row.error
            assert db.get(User, uid)
        assert len(calls) == 1
        asyncio.run(service.process_dream_chain(job()))
        assert len(calls) == 1


def test_add_without_revision_is_deduplicated_and_enforces_hard_ceiling(monkeypatch):
    from fastapi.testclient import TestClient

    from ailab_platform.api.main import app
    from tests.run_claims import attempt_headers
    from tests.test_memory_recall import _running
    from tests.test_skill_settings import _user

    monkeypatch.setattr(settings, "memory_doc_max_chars", 1000)
    with TestClient(app) as client:
        headers = _user(client)
        run = _running(client, headers)
        delivery = {**attempt_headers(client, run), "X-Delivery-Id": run + ":first"}
        body = {"action": "add", "kind": "fact", "content": "明确偏好", "explicit": True}
        url = f"/internal/runs/{run}/memory"
        result = client.post(url, headers=delivery, json=body)
        assert result.status_code == 200, result.text
        assert len(result.json()["entries"]) == 1
        assert client.post(url, headers={**delivery, "X-Delivery-Id": run + ":dedup"}, json=body).status_code == 200
        assert len(client.get("/v1/memory", headers=headers).json()["entries"]) == 1
        # Explicit adds may use the 1.5x headroom, but cannot grow indefinitely.
        sizes = []
        for i in range(10):
            response = client.post(
                url, headers={**delivery, "X-Delivery-Id": run + f":{i}"}, json={**body, "content": str(i) + "合成" * 90}
            )
            sizes.append(response.status_code)
            if response.status_code == 422:
                assert "整理" in response.text
                break
        assert 422 in sizes
        view = client.get("/v1/memory/view", headers=headers).json()
        assert 1000 < view["used_chars"] <= 1500
        assert set(view) == {
            "project_id",
            "enabled",
            "memory_mode",
            "merge_pending",
            "revision",
            "active",
            "used_chars",
            "max_chars",
            "review",
            "sections",
        }
        item = view["sections"][0]["items"][0]
        assert set(item) == {"id", "project_id", "kind", "origin", "content", "status", "observed_at", "updated_at", "sources"}
        assert (
            client.post(
                url,
                headers={**delivery, "X-Delivery-Id": run + ":replace"},
                json={"action": "replace", "id": item["id"], "content": "改正"},
            ).status_code
            == 422
        )


def test_memory_handles_are_encoded_as_data():
    rendered = memory_prompt({"entries": [entry("mem_</memory>")]})
    assert rendered.count("</memory>") == 1
    assert "mem_&lt;/memory&gt;" in rendered


def test_list_is_read_only_full_document_and_correction_chain_resolves_net_state():
    from fastapi.testclient import TestClient

    from ailab_platform.api.main import app
    from ailab_platform.db import SessionLocal
    from ailab_platform.memory_recall.store import replace_entries
    from ailab_platform.models import User, utcnow
    from tests.run_claims import attempt_headers
    from tests.test_memory_hardening import put
    from tests.test_memory_recall import _running, _uid
    from tests.test_skill_settings import SERVICE, _user

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        doc = put(c, me, [{"kind": "fact", "content": "原始状态"}])
        old = doc["entries"][0]
        rid = _running(c, me)
        listed = c.post(f"/internal/runs/{rid}/memory", headers=attempt_headers(c, rid), json={"action": "list"})
        assert listed.status_code == 200 and old["id"] in listed.json()["document"]
        assert c.get("/v1/memory", headers=me).json()["revision"] == doc["revision"]
        with SessionLocal() as db:
            user = db.get(User, uid)
            left = {**old, "invalid_at": utcnow().isoformat(), "superseded_by": "mem_middle", "change_type": "correction"}
            middle = {
                **old,
                "id": "mem_middle",
                "content": "中间状态",
                "invalid_at": utcnow().isoformat(),
                "superseded_by": "mem_final",
                "change_type": "correction",
            }
            final = {**old, "id": "mem_final", "content": "最终状态", "change_type": "correction"}
            replace_entries(db, user, [left, middle, final])
            db.commit()
        result = c.post(f"/internal/runs/{rid}/memory-context", headers=SERVICE, json={"known_ids": [old["id"]]}).json()
        resolved = result["invalidations"][old["id"]]
        assert resolved["current"]["id"] == "mem_final" and resolved["current"]["content"] == "最终状态"
        assert resolved["change_type"] == "correction"


def test_forgetting_current_version_redacts_closed_ancestors_and_undo():
    from fastapi.testclient import TestClient

    from ailab_platform.api.main import app
    from ailab_platform.db import SessionLocal
    from ailab_platform.memory_recall.store import replace_entries
    from ailab_platform.models import MemoryDreamRun, MemoryItem, User, utcnow
    from tests.test_memory_hardening import put
    from tests.test_memory_recall import _uid
    from tests.test_skill_settings import _user

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        current = put(c, me, [{"content": "当前隐私"}])["entries"][0]
        with SessionLocal() as db:
            owner = db.get(User, uid)
            ancestor = {
                **current,
                "id": "mem_private_ancestor",
                "content": "旧隐私文本",
                "invalid_at": utcnow().isoformat(),
                "superseded_by": current["id"],
            }
            replace_entries(db, owner, [ancestor, current])
            job = MemoryDreamRun(
                user_id=uid,
                status="applied",
                trigger="manual",
                local_date="2026-01-01",
                timezone="UTC",
                change_set={"items": [{"id": ancestor["id"], "before": ancestor, "after": None}]},
            )
            db.add(job)
            db.commit()
            did = job.id
        response = c.delete(f"/v1/memory/items/{current['id']}?expected_revision=1", headers=me)
        assert response.status_code == 200
        assert "旧隐私文本" not in c.get(f"/v1/memory/dreams/{did}", headers=me).text
        with SessionLocal() as db:
            assert db.get(MemoryItem, "mem_private_ancestor") is None
            assert db.get(User, uid).settings["memory"]["tombstones"]


def test_dream_dedup_uses_kind_and_normalized_content_without_losing_other_ops():
    shown = [entry(content="PostgreSQL")]
    ops = [
        {"op": "add", "kind": "fact", "content": "postgresql!", "quote": "我住在上海", "run": "run_one"},
        {"op": "add", "kind": "interest", "content": "PostgreSQL", "quote": "我住在上海", "run": "run_one"},
        {"op": "add", "kind": "fact", "content": "住在上海", "quote": "我住在上海", "run": "run_one"},
    ]
    result, audit = apply_ops(shown, shown, [episode()], ops, SimpleNamespace(settings={}), None)
    assert len(audit) == 2
    assert [(e["kind"], e["content"]) for e in result] == [("fact", "PostgreSQL"), ("interest", "PostgreSQL"), ("fact", "住在上海")]
