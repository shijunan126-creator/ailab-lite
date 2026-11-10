"""Transitions keep the past in tense, deletes need a code, reason codes are counted, not enforced (ADR 0036)."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.api.routes_memory import memory_for
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.memory_dream.document import apply_ops
from ailab_platform.memory_recall.invalidations import for_handles
from ailab_platform.memory_recall.tombstones import fact_hash
from ailab_platform.models import MemoryDreamRun, MemoryEpisode, MemoryItem, User
from tests.memory_fixtures import make_dream
from tests.test_memory_hardening import put
from tests.test_memory_recall import _seed, _uid
from tests.test_skill_settings import _user

SAID = "我从券商离职了，现在在做独立研究；记忆模块那个项目也做完了"


def line(identity, kind="fact", origin="inferred", content="在券商做量化研究", **kw):
    return {
        "id": identity,
        "kind": kind,
        "origin": origin,
        "content": content,
        "observed_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "project_id": None,
        "evidence_run_ids": ["run_old"],
        **kw,
    }


def episode():
    observed = datetime(2026, 11, 1, tzinfo=UTC)
    return MemoryEpisode(id="epi_t", run_id="run_new", observed_at=observed, digest=json.dumps({"user_said": SAID}))


def transition(**kw):
    return {
        "op": "transition",
        "id": "mem_job",
        "past": "2026-11 前在券商做量化研究",
        "now": "在做独立研究",
        "quote": "我从券商离职了",
        "run": "run_new",
        "r": "E",
        **kw,
    }


def nobody(tombstones=()):
    return SimpleNamespace(settings={"memory": {"tombstones": [{"hash": fact_hash(t)} for t in tombstones]}})


def test_transition_keeps_the_past_in_tense_and_adds_the_present():
    shown = [line("mem_job")]
    codes = {}
    result, audit = apply_ops(shown, shown, [episode()], [transition()], nobody(), None, codes)
    old, past, now = result
    assert old["superseded_reason"] == "correction" and old["superseded_by"] == past["id"] and old["invalid_at"]
    assert past["content"] == "2026-11 前在券商做量化研究" and past["created_by"] == "transition" and past["kind"] == "fact"
    assert now["content"] == "在做独立研究" and now["created_by"] == "addition"
    assert past["evidence_run_ids"] == now["evidence_run_ids"] == ["run_old", "run_new"]
    assert audit == [{"op": "transition", "ids": ["mem_job"], "new_id": past["id"], "now_id": now["id"]}]
    assert codes == {"E": 1}


def test_an_ended_project_moves_to_past_experience():
    shown = [line("mem_job", kind="project", content="推进记忆模块上线")]
    op = transition(past="2026-11 完成记忆模块上线", now=None, past_kind="fact", quote="记忆模块那个项目也做完了")
    result, _ = apply_ops(shown, shown, [episode()], [op], nobody(), None)
    assert len(result) == 2 and result[1]["kind"] == "fact" and result[1]["created_by"] == "transition"


def test_a_present_that_clashes_keeps_only_the_past():
    shown = [line("mem_job"), line("mem_now", content="在做独立研究")]
    result, audit = apply_ops(shown, shown, [episode()], [transition()], nobody(), None)
    assert len(result) == 3 and audit[0]["now_id"] is None
    tombstoned = [line("mem_job")]
    result, audit = apply_ops(tombstoned, tombstoned, [episode()], [transition()], nobody(["在做独立研究"]), None)
    assert len(result) == 2 and result[1]["content"].startswith("2026-11 前") and audit[0]["now_id"] is None


def test_a_protected_line_changes_only_on_a_newer_quote_and_stays_protected():
    shown = [line("mem_job", origin="explicit")]
    result, _ = apply_ops(shown, shown, [episode()], [transition()], nobody(), None)
    assert result[1]["origin"] == "explicit" and result[2]["origin"] == "inferred"
    newer = [line("mem_job", origin="explicit", updated_at="2027-01-01T00:00:00+00:00")]
    assert apply_ops(newer, newer, [episode()], [transition()], nobody(), None)[1] == []
    unquoted = [line("mem_job")]
    assert apply_ops(unquoted, unquoted, [episode()], [transition(quote="助手说你离职了")], nobody(), None)[1] == []


def test_delete_needs_a_known_code_and_wrong_needs_the_users_words():
    shown = [line("mem_a"), line("mem_b", content="重复的一行"), line("mem_c", content="错的一行")]
    codes = {}
    ops = [
        {"op": "delete", "id": "mem_a"},  # no code: refused, counted as "?"
        {"op": "delete", "id": "mem_a", "reason": "outdated"},  # the old field is gone
        {"op": "delete", "id": "mem_c", "r": "I"},  # wrong without the user's words: refused
        {"op": "delete", "id": "mem_b", "r": "H"},
        {"op": "delete", "id": "mem_c", "r": "I", "quote": "我从券商离职了", "run": "run_new"},
        {"op": "add", "kind": "fact", "content": "做独立研究", "quote": "现在在做独立研究", "run": "run_new"},  # no code: still applied
    ]
    result, audit = apply_ops(shown, shown, [episode()], ops, nobody(), None, codes)
    assert [a["ids"] for a in audit if a["op"] == "delete"] == [["mem_b"], ["mem_c"]]
    assert any(e["content"] == "做独立研究" for e in result)
    assert codes == {"?": 3, "I": 2, "H": 1}


def test_a_dream_batch_applies_a_transition_notifies_counts_codes_and_reverts(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        old = put(c, me, [{"kind": "fact", "content": "在券商做量化研究", "origin": "inferred"}])["entries"][0]
        rid = _seed(uid, SAID, days_ago=0)
        test_run = _seed(uid, "用三句话解释什么是对照组", days_ago=0)
        other = _seed(uid, "今天几号", days_ago=0)

        async def chat(*args):
            return json.dumps(
                {
                    "ops": [transition(id=old["id"], run=rid)],
                    "ignored_runs": [{"run": test_run, "r": "T"}, {"run": other, "r": "Z"}],
                }
            ), {}

        monkeypatch.setattr(service, "_chat", chat)
        with SessionLocal() as db:
            job = make_dream(user_id=uid, local_date="2026-11-10", timezone="UTC", status="pending", trigger="manual")
            db.add(job)
            db.commit()
            did = job.id
        asyncio.run(service.process_dream_chain(did))
        contents = sorted(e["content"] for e in c.get("/v1/memory", headers=me).json()["entries"])
        assert contents == ["2026-11 前在券商做量化研究", "在做独立研究"]
        with SessionLocal() as db:
            report = db.get(MemoryDreamRun, did).report
            assert report["codes"] == {"E": 1, "T": 1, "?": 1}
            assert report["ignored_runs"] == {test_run: "test", other: "no_signal"}
            owner = db.get(User, uid)
            # an open conversation that saw the old line gets the existing correction notice, pointing at the past tense
            resolved = for_handles(db, uid, [old["id"]], memory_for(owner), None, "shared")[old["id"]]
            assert resolved["change_type"] == "correction" and resolved["current"]["content"] == "2026-11 前在券商做量化研究"
            assert db.get(MemoryItem, resolved["current"]["id"]).created_by == "transition"
        view = c.get("/v1/memory/view", headers=me).json()
        assert {i["created_by"] for s in view["sections"] for i in s["items"]} == {"transition", "addition"}
        assert c.post(f"/v1/memory/dreams/{did}/revert", headers=me).status_code == 200
        assert [e["content"] for e in c.get("/v1/memory", headers=me).json()["entries"]] == ["在券商做量化研究"]


def test_page_edit_can_move_an_ended_project_to_past_experience(monkeypatch):
    from tests.test_memory_summary import edit, scripted

    with TestClient(app) as c:
        me = _user(c)
        project_line = put(c, me, [{"kind": "project", "content": "推进记忆模块上线", "origin": "explicit"}])["entries"][0]
        scripted(monkeypatch, ops=[{"op": "replace", "id": project_line["id"], "content": "2026-11 完成记忆模块上线", "kind": "fact"}])
        assert edit(c, me, "记忆模块项目做完了")["status"] in {"pending", "running", "applied"}
        entries = c.get("/v1/memory", headers=me).json()["entries"]
        assert [(e["kind"], e["content"]) for e in entries] == [("memory", "2026-11 完成记忆模块上线")]
