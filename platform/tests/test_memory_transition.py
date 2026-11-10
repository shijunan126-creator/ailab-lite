"""Transitions keep the past in tense, deletes need a code, reason codes are counted, not enforced (ADR 0036)."""

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.api.routes_memory import memory_for
from ailab_platform.contracts.memory import MemoryEntryInput
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


def line(identity, kind="fact", content="在券商做量化研究", **kw):
    return {
        "id": identity,
        "kind": kind,
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


def test_any_line_moves_to_the_past_but_only_on_the_users_words():
    # one kind of line (ADR 0037): a line the user typed changes like any other, still only on what the user said
    shown = [line("mem_job")]
    result, audit = apply_ops(shown, shown, [episode()], [transition()], nobody(), None)
    assert len(audit) == 1 and all("origin" not in e for e in result[1:])
    assert apply_ops(shown, shown, [episode()], [transition(quote="助手说你离职了")], nobody(), None)[1] == []


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


def test_the_one_time_resort_rewrites_splits_and_drops_lines_without_quotes():
    shown = [
        line("mem_ppt", kind="project", content="要求把奈飞营收做成 PPT（已交付）"),
        line("mem_ask", kind="interest", content="关注乐信经营（2026-11-08 两次询问）"),
        line("mem_gold", kind="interest", content="关注黄金走势（询问）"),
        line("mem_done", kind="project", content="推进记忆模块上线"),
        line("mem_mix", kind="interest", content="关注美股科技公司财报，偏好用图表呈现"),
    ]
    ops = [
        {"op": "delete", "id": "mem_ppt", "r": "K"},
        {"op": "update", "id": "mem_ask", "content": "关注中概金融科技公司的经营风险", "r": "F"},
        {"op": "delete", "id": "mem_gold", "r": "W"},
        {"op": "transition", "id": "mem_done", "past": "2026-11 完成记忆模块上线", "past_kind": "fact", "r": "E"},
        {"op": "update", "id": "mem_mix", "content": "关注美股科技公司财报", "r": "F"},
        {"op": "add", "kind": "preference", "content": "数据类回答偏好用图表呈现", "from": ["mem_mix"], "r": "F"},  # split
        {"op": "add", "kind": "fact", "content": "新的一行", "r": "B"},  # a re-sort adds nothing new
        {"op": "add", "kind": "fact", "content": "编造的一行", "from": ["mem_missing"]},  # split of an unknown line
        {"op": "delete", "id": "mem_ask", "r": "I"},  # wrong needs the user's words: unknown here
    ]
    codes = {}
    result, audit = apply_ops(shown, shown, [], ops, nobody(), None, codes, cleanup=True)
    live = {e["content"]: e for e in result if not e.get("invalid_at")}
    assert set(live) == {"关注中概金融科技公司的经营风险", "2026-11 完成记忆模块上线", "关注美股科技公司财报", "数据类回答偏好用图表呈现"}
    rewritten = live["关注中概金融科技公司的经营风险"]
    assert rewritten["source_quote"] == "" and rewritten["evidence_run_ids"] == ["run_old"]
    assert live["数据类回答偏好用图表呈现"]["kind"] == "preference" and live["数据类回答偏好用图表呈现"]["evidence_run_ids"] == ["run_old"]
    assert live["2026-11 完成记忆模块上线"]["kind"] == "fact" and live["2026-11 完成记忆模块上线"]["created_by"] == "transition"
    assert sorted(a["op"] for a in audit) == ["add", "delete", "delete", "transition", "update", "update"]


def test_daily_batches_still_delete_only_duplicates_and_user_corrections():
    shown = [line("mem_ppt", kind="project", content="要求把奈飞营收做成 PPT（已交付）")]
    codes = {}
    ops = [{"op": "delete", "id": "mem_ppt", "r": "K"}, {"op": "update", "id": "mem_ppt", "content": "改写", "r": "F"}]
    result, audit = apply_ops(shown, shown, [episode()], ops, nobody(), None, codes)
    assert audit == [] and not result[0].get("invalid_at")
    assert codes == {"?": 1, "F": 1}


def test_every_stored_line_is_one_kind_and_legacy_markers_survive_rewrites():
    from ailab_platform.memory_recall.store import replace_entries

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        mid = "mem_m" + uid[4:20]
        with SessionLocal() as db:
            replace_entries(
                db,
                db.get(User, uid),
                [
                    {"id": mid, "kind": "interest", "content": "关注乐信", "origin": "explicit", "origin_before": "explicit"},
                    {"id": "mem_n" + uid[4:20], "kind": "fact", "content": "没有 origin 的夜间行", "source": "nightly"},
                ],
            )
            db.commit()
        current = c.get("/v1/memory", headers=me).json()
        body = [{k: v for k, v in e.items() if k in MemoryEntryInput.model_fields} for e in current["entries"]]
        saved = c.put("/v1/memory", headers=me, json={"expected_revision": current["revision"], "entries": body})
        assert saved.status_code == 200, saved.text
        with SessionLocal() as db:
            assert db.get(MemoryItem, mid).metadata_["origin_before"] == "explicit"
            assert {db.get(MemoryItem, i).origin for i in (mid, "mem_n" + uid[4:20])} == {"memory"}
