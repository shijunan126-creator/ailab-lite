"""Memory invariants, including the 13 red review contracts from main@0659101."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from ailab_platform.api.main import app
from ailab_platform.api.routes_memory import memory_for
from ailab_platform.contracts.memory import DreamChange, DreamPlan
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.memory_recall import embeddings
from ailab_platform.models import Conversation, MemoryDreamRun, MemoryEpisode, MemoryWorkItem, Run, User
from tests.run_claims import attempt_headers
from tests.test_memory_recall import _conversation, _running, _seed, _uid
from tests.test_skill_settings import _user


def put(c, me, entries, *, auto_save=True):
    current = c.get("/v1/memory", headers=me).json()
    out = c.put(
        "/v1/memory",
        headers=me,
        json={
            "expected_revision": current["revision"],
            "enabled": True,
            "auto_save": auto_save,
            "entries": entries,
        },
    )
    assert out.status_code == 200, out.text
    return out.json()


def dream_row(uid, **kw):
    return MemoryDreamRun(
        user_id=uid,
        local_date="2026-10-28",
        timezone="Asia/Shanghai",
        status="pending",
        trigger="cron",
        **kw,
    )


def install_extractor(monkeypatch, seen=None):
    async def extract(episodes, active, user):
        fitted = service._fit_episodes(episodes, service.settings.memory_dream_input_chars)
        if seen is not None:
            seen.extend(e.run_id for e in fitted)
        e = fitted[-1]
        return (
            [
                {
                    "text": "用户喜欢蓝色主题",
                    "kind": "user",
                    "confidence": 0.95,
                    "evidence_run_ids": [e.run_id],
                    "evidence_episode_ids": [e.id],
                    "entities": [],
                    "source_quote": service._user_part(e.digest)[:20],
                }
            ],
            {},
            "fake",
        )

    async def judge(*args):
        return {}, {}

    monkeypatch.setattr(service, "_extract", extract)
    monkeypatch.setattr(service, "_judge", judge)
    monkeypatch.setattr(service.settings, "memory_dream_min_runs", 1)


def test_paused_memory_is_not_disclosed_by_the_agent_list_tool():
    with TestClient(app) as c:
        me = _user(c)
        put(c, me, [{"content": "暂停后不应给模型的事实", "suppressed": True}])
        rid = _running(c, me)
        out = c.post(
            f"/internal/runs/{rid}/memory",
            headers=attempt_headers(c, rid),
            json={"action": "list"},
        )
        assert out.status_code == 200
        assert "暂停后不应给模型的事实" not in out.text


def test_nightly_extraction_obeys_auto_save_off(monkeypatch):
    install_extractor(monkeypatch)
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        put(c, me, [], auto_save=False)
        _seed(uid, "用户喜欢蓝色主题", days_ago=0)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream(did))
        assert c.get("/v1/memory", headers=me).json()["entries"] == []


def test_budget_trimmed_episodes_remain_eligible_next_time(monkeypatch):
    seen = []
    install_extractor(monkeypatch, seen)
    monkeypatch.setattr(service.settings, "memory_dream_input_chars", 8000)
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        ids = [_seed(uid, str(i) + "甲" * 3999, "乙" * 2000, days_ago=0) for i in range(3)]
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream(did))
        with SessionLocal() as db:
            last = service._last_success(db, uid)
            assert last is not None and last.status == "applied"
            eligible = {r.id for r in service._new_runs(db, uid, last, datetime.now(UTC))}
        assert set(ids) <= set(seen) | eligible, {
            "seen": seen,
            "eligible": sorted(eligible),
            "all": ids,
        }


def test_deleting_source_redacts_dream_snapshot_quotes():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid)
        rid = _seed(uid, "原始隐私原话 UNIQUE_SOURCE_QUOTE", conversation_id=conv)
        doc = put(
            c,
            me,
            [
                {
                    "content": "用户偏好蓝色",
                    "evidence_run_ids": [rid],
                    "source_quote": "UNIQUE_SOURCE_QUOTE",
                }
            ],
        )
        with SessionLocal() as db:
            row = dream_row(uid, before_memory=doc, after_memory=doc)
            row.status = "applied"
            db.add(row)
            db.commit()
            did = row.id
        assert c.delete(f"/v1/conversations/{conv}", headers=me).status_code == 204
        current = c.get("/v1/memory", headers=me).json()
        assert current["entries"][0]["source_quote"] == ""
        detail = c.get(f"/v1/memory/dreams/{did}", headers=me)
        assert detail.status_code == 200
        assert "UNIQUE_SOURCE_QUOTE" not in detail.text


def test_inflight_dream_cannot_resurrect_a_deleted_fact():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        content = "用户喜欢蓝色主题"
        doc = put(c, me, [{"content": content}])
        # The candidate passed screening before a concurrent user deletion.
        facts = service._screen([{"text": content}], [])
        assert facts
        put(c, me, [])
        with SessionLocal() as db:
            # This is the post-model apply boundary, after the user lock is reacquired.
            with pytest.raises(service.DreamError, match="已被用户删除"):
                service._apply(db, db.get(User, uid), DreamPlan(changes=[DreamChange(op="add", content=content)]), {})
            db.rollback()
        assert c.get("/v1/memory", headers=me).json()["entries"] == [], doc["revision"]


def test_extract_quote_must_belong_to_the_claimed_source(monkeypatch):
    now = datetime.now(UTC)
    episodes = [
        MemoryEpisode(
            id="epi_a",
            run_id="run_a",
            observed_at=now,
            digest="用户：我喜欢蓝色",
            content_hash="a",
        ),
        MemoryEpisode(
            id="epi_b",
            run_id="run_b",
            observed_at=now,
            digest="用户：我住在上海",
            content_hash="b",
        ),
    ]

    async def chat(*args):
        return json.dumps(
            {
                "facts": [
                    {
                        "text": "用户喜欢蓝色",
                        "source_quote": "我喜欢蓝色",
                        "confidence": 0.99,
                        "evidence_run_ids": ["run_b"],
                        "evidence_episode_ids": ["epi_b"],
                    }
                ]
            }
        ), {}

    monkeypatch.setattr(service, "_chat", chat)
    out, _, _ = asyncio.run(service._extract(episodes, [], User(id="usr_probe")))
    assert not out or (out[0]["evidence_run_ids"] == ["run_a"] and out[0]["evidence_episode_ids"] == ["epi_a"]), out


def test_agent_replace_rebinds_evidence_to_the_correction_run():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        old_run = _seed(uid, "我住在上海")
        doc = put(c, me, [{"content": "用户住在上海", "evidence_run_ids": [old_run]}])
        rid = _running(c, me)
        out = c.post(
            f"/internal/runs/{rid}/memory",
            headers={**attempt_headers(c, rid), "X-Delivery-Id": f"{rid}:replace"},
            json={
                "action": "replace",
                "id": doc["entries"][0]["id"],
                "content": "用户现在住在北京",
                "expected_revision": doc["revision"],
            },
        )
        assert out.status_code == 200, out.text
        assert out.json()["entries"][0]["evidence_run_ids"] == [rid]


def test_late_finished_run_is_not_skipped_by_created_at_cursor():
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        now = datetime.now(UTC)
        old = _seed(uid, "较早开始后来才完成", days_ago=0.2)
        newer = _seed(uid, "较晚开始但先完成", days_ago=0.1)
        with SessionLocal() as db:
            last = dream_row(uid, cursor_run_id=newer, finished_at=now - timedelta(hours=1))
            last.status = "applied"
            db.add(last)
            db.get(Run, old).status = "running"
            db.commit()
            # A background/manual run can finish after the prior dream's snapshot.
            db.get(Run, old).status = "succeeded"
            db.commit()
            eligible = [r.id for r in service._new_runs(db, uid, last, now)]
            assert old in eligible


def test_disabled_accounts_do_not_starve_embedding_backfill(monkeypatch):
    monkeypatch.setattr(embeddings.settings, "memory_embedding_base_url", "http://unused.invalid/v1")
    monkeypatch.setattr(embeddings.settings, "memory_embedding_model", "review-model")
    monkeypatch.setattr(embeddings, "embed", lambda texts, **kw: [[1.0, 0.0] for _ in texts])
    with TestClient(app) as c:
        enabled_user, disabled_user = _user(c), _user(c)
        good_uid, disabled_uid = _uid(c, enabled_user), _uid(c, disabled_user)
        good_run = _seed(good_uid, "应该索引的历史", days_ago=1)
        put(c, disabled_user, [], auto_save=False)
        doc = c.get("/v1/memory", headers=disabled_user).json()
        c.put(
            "/v1/memory",
            headers=disabled_user,
            json={
                "expected_revision": doc["revision"],
                "enabled": False,
                "entries": [],
            },
        )
        with SessionLocal() as db:
            db.add_all(
                [
                    Run(
                        user_id=disabled_uid,
                        prompt="关闭记忆的较新轮次",
                        status="succeeded",
                    )
                    for _ in range(500)
                ]
            )
            db.commit()
        assert embeddings.index_pending(user_id=good_uid) == 1  # endpoint and candidate work
        # Remove the control vector so global backfill must find it.
        with SessionLocal() as db:
            embeddings.delete_for_runs(db, [good_run])
            db.commit()
        assert embeddings.index_pending() > 0


def test_paused_fact_is_excluded_from_dream_model_inputs():
    paused = {"id": "mem_pause", "content": "不应被后台模型参考", "suppressed": True}
    assert service._active([paused]) == []


def test_conflict_replacement_and_invalidation_are_an_atomic_change(monkeypatch):
    install_extractor(monkeypatch)
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        old = put(c, me, [{"content": f"项目{i}旧状态"} for i in range(3)])["entries"]
        _seed(uid, "项目状态已全部替换", days_ago=0)

        async def extract(episodes, active, user):
            return (
                [
                    {
                        "text": f"项目{i}新状态",
                        "kind": "memory",
                        "confidence": 0.95,
                        "evidence_run_ids": [episodes[0].run_id],
                        "evidence_episode_ids": [episodes[0].id],
                        "entities": [],
                        "source_quote": "项目状态已全部替换",
                    }
                    for i in range(3)
                ],
                {},
                "fake",
            )

        async def judge(*args):
            return {i: {"contradicted_ids": [e["id"]]} for i, e in enumerate(old)}, {}

        monkeypatch.setattr(service, "_extract", extract)
        monkeypatch.setattr(service, "_judge", judge)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream(did))
        entries = c.get("/v1/memory", headers=me).json()["entries"]
        active = {e["content"] for e in entries if not e["invalid_at"]}
        for i in range(3):
            assert not ({f"项目{i}旧状态", f"项目{i}新状态"} <= active), active


def test_model_bootstrap_filters_paused_and_invalid_facts():
    user = User(
        settings={
            "memory": {
                "entries": [
                    {"id": "mem_a", "content": "暂停", "suppressed": True},
                    {
                        "id": "mem_b",
                        "content": "失效",
                        "invalid_at": "2026-10-28T00:00:00+00:00",
                    },
                    {"id": "mem_c", "content": "有效"},
                ]
            }
        }
    )
    assert [e["id"] for e in memory_for(user, for_model=True)["entries"]] == ["mem_c"]


def test_dream_does_not_hold_sqlite_writer_during_model_wait(monkeypatch):
    with SessionLocal() as db:
        if db.get_bind().dialect.name != "sqlite":
            pytest.skip("SQLite writer contract; PostgreSQL concurrency is tested separately")
    install_extractor(monkeypatch)
    observed_errors = []
    with TestClient(app) as c:
        uid, other_uid = _uid(c, _user(c)), _uid(c, _user(c))
        _seed(uid, "触发夜间记忆抽取", days_ago=0)

        async def extract(*args):
            # An unrelated account write arriving while the model call is in flight.
            # Shorten the test's wait; production waits 30s before raising the same error.
            with SessionLocal() as concurrent:
                concurrent.execute(text("PRAGMA busy_timeout = 50"))
                try:
                    concurrent.execute(
                        text("UPDATE users SET display_name = :name WHERE id = :uid"),
                        {"name": "unrelated account", "uid": other_uid},
                    )
                    concurrent.commit()
                except OperationalError as exc:
                    observed_errors.append(str(exc))
            return [], {}, "fake"

        monkeypatch.setattr(service, "_extract", extract)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream(did))
        assert not observed_errors, observed_errors


def test_projects_are_isolated_in_fact_context_history_and_raw_detail():
    from tests.test_skill_settings import SERVICE

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        projects = [c.post("/v1/projects", headers=me, json={"name": f"项目{i}"}).json()["id"] for i in range(2)]
        convs = [c.post("/v1/conversations", headers=me, json={"project_id": p}).json()["id"] for p in projects]
        secret = _seed(uid, "BLUEPRINT 项目机密", conversation_id=convs[1])
        put(c, me, [{"content": "BLUEPRINT 项目机密", "project_id": projects[1]}, {"kind": "user", "content": "回答用中文"}])
        created = c.post(f"/v1/conversations/{convs[0]}/messages", headers=me, json={"prompt": "BLUEPRINT"}).json()
        rid = created["id"]
        bootstrap = c.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()
        assert [e["content"] for e in bootstrap["memory"]["entries"]] == ["回答用中文"]
        out = c.post(f"/internal/runs/{rid}/recall", headers=SERVICE, json={"query": "BLUEPRINT"}).json()
        assert out["results"] == []
        assert c.post(f"/internal/runs/{rid}/recall", headers=SERVICE, json={"run_id": secret}).status_code == 404
        doc = c.get("/v1/memory", headers=me).json()
        assert (
            c.put(
                "/v1/memory",
                headers=me,
                json={"expected_revision": doc["revision"], "entries": [{"kind": "user", "content": "回答用中文"}], "project_only": True},
            ).status_code
            == 200
        )
        assert c.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()["memory"]["entries"] == []


def test_dialog_policy_freezes_no_write_and_live_read_disable():
    from tests.test_skill_settings import SERVICE

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid)
        put(c, me, [{"kind": "user", "content": "始终用中文回答"}])
        assert c.patch(f"/v1/conversations/{conv}", headers=me, json={"memory_policy": {"read": False, "write": False}}).status_code == 200
        rid = c.post(f"/v1/conversations/{conv}/messages", headers=me, json={"prompt": "hello"}).json()["id"]
        assert c.get(f"/internal/runs/{rid}/model-input", headers=SERVICE).json()["memory"]["entries"] == []
        c.patch(f"/v1/conversations/{conv}", headers=me, json={"memory_policy": {"read": True, "write": True}})
        with SessionLocal() as db:
            assert db.get(Run, rid).options["memory_policy"]["write"] is False
            assert service._new_runs(db, uid, None, datetime.now(UTC)) == []


def test_sensitive_inferences_need_user_confirmation_and_history_is_temporal():
    from ailab_platform.memory_recall.policy import model_view

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        with SessionLocal() as db:
            service._apply(db, db.get(User, uid), DreamPlan(changes=[DreamChange(op="add", content="用户患有慢性病")]), {})
            db.commit()
        doc = c.get("/v1/memory", headers=me).json()
        assert doc["entries"][0]["status"] == "review"
        assert model_view(doc)["entries"] == []
        put(c, me, [{"id": doc["entries"][0]["id"], "content": "用户患有慢性病", "status": "active"}])
        assert model_view(c.get("/v1/memory", headers=me).json())["entries"]
        old = {
            "id": "mem_old",
            "content": "旧状态",
            "valid_from": "2026-01-01T00:00:00+00:00",
            "invalid_at": "2026-06-01T00:00:00+00:00",
            "superseded_by": "mem_new",
        }
        assert model_view({"entries": [old]}, as_of=datetime(2026, 3, 1, tzinfo=UTC))["entries"] == [old]
        assert model_view({"entries": [old]})["entries"] == []


def test_dream_claim_is_exclusive_and_pending_candidates_survive_budget(monkeypatch):
    install_extractor(monkeypatch)
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        rid = _seed(uid, "用户信息", days_ago=0)
        with SessionLocal() as db:
            item = MemoryWorkItem(
                run_id=rid,
                user_id=uid,
                state="pending",
                source_end=4,
                candidates=[
                    {
                        "text": f"持久事实{i}",
                        "kind": "memory",
                        "confidence": 0.95,
                        "evidence_run_ids": [rid],
                        "evidence_episode_ids": [],
                        "entities": [],
                        "source_quote": "用户信息",
                    }
                    for i in range(7)
                ],
            )
            row = dream_row(uid)
            db.add_all([item, row])
            db.commit()
            did = row.id

        async def no_extract(*args):
            raise AssertionError("cached candidates must not be extracted or paid for again")

        monkeypatch.setattr(service, "_extract", no_extract)
        asyncio.run(service.process_dream(did))
        with SessionLocal() as db:
            assert db.get(MemoryDreamRun, did).status == "applied"
            assert len(db.get(MemoryWorkItem, rid).candidates) == 2
            row = db.get(MemoryDreamRun, did)
            row.status = "running"
            row.claim_token = "other_owner"
            row.claimed_until = datetime.now(UTC) + timedelta(minutes=3)
            db.commit()
        asyncio.run(service.process_dream(did))
        with SessionLocal() as db:
            assert db.get(MemoryDreamRun, did).claim_token == "other_owner"


def test_deleting_fact_redacts_undo_and_replay_and_undo_refuses_newer_changes():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        before = put(c, me, [{"content": "删除后不能复活 UNIQUE_REMOVED"}])
        with SessionLocal() as db:
            row = dream_row(uid, before_memory=before, after_memory=before)
            row.status = "applied"
            db.add(row)
            db.commit()
            did = row.id
        put(c, me, [])
        detail = c.get(f"/v1/memory/dreams/{did}", headers=me).text
        assert "UNIQUE_REMOVED" not in detail
        assert c.post(f"/v1/memory/dreams/{did}/revert", headers=me).status_code == 409


def test_real_dream_calls_are_reserved_settled_and_retries_are_distinct(monkeypatch):
    from sqlalchemy import select

    from ailab_platform.llm import LLMResponse
    from ailab_platform.models import ModelUsage, UsageReservation

    class Model:
        async def chat(self, messages, tools, model, **kw):
            if "memory extractor" in messages[0]["content"]:
                ep = json.loads(messages[1]["content"].split("新 episodes：\n")[1].split("\n\n只返回")[0])[0]
                return LLMResponse(
                    content=json.dumps(
                        {
                            "facts": [
                                {
                                    "text": "用户喜欢蓝色",
                                    "kind": "user",
                                    "confidence": 0.95,
                                    "source_quote": "我喜欢蓝色",
                                    "evidence_run_ids": [ep["run_id"]],
                                    "evidence_episode_ids": [ep["id"]],
                                }
                            ]
                        }
                    ),
                    usage={"prompt_tokens": 40, "completion_tokens": 10, "cached_tokens": 2},
                )
            return LLMResponse(
                content='{"results":[{"index":0,"supported":true,"duplicate_ids":[],"contradicted_ids":[]}]}',
                usage={"prompt_tokens": 20, "completion_tokens": 5},
            )

    monkeypatch.setattr(service, "llm_for", lambda model: Model())
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        _seed(uid, "我喜欢蓝色", days_ago=0)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream(did))
        with SessionLocal() as db:
            assert db.get(MemoryDreamRun, did).status == "applied"
            calls = list(db.scalars(select(ModelUsage).where(ModelUsage.job_id == did)))
            assert len(calls) == 2
            assert len({r.call_id for r in calls}) == 2
            assert all(r.subject == "memory_job" and r.status == "settled" and r.weighted_amount > 0 for r in calls)
            assert all(db.get(UsageReservation, r.call_id).status == "settled" for r in calls)


def test_judge_denies_unsupported_inferences_even_with_valid_quotes(monkeypatch):
    async def chat(*args):
        return '{"results":[{"index":0,"supported":false,"duplicate_ids":[],"contradicted_ids":[]}]}', {}

    monkeypatch.setattr(service, "_chat", chat)
    verdicts, _ = asyncio.run(
        service._judge([{"text": "用户患有某疾病", "source_quote": "假设有人患有某疾病"}], [], User(id="usr_test"), "fake")
    )
    assert verdicts[0]["supported"] is False


def test_long_source_segments_and_steers_reach_the_extractor_without_gaps(monkeypatch):
    from ailab_platform.models import QueuedMessage

    seen = []

    async def extract(episodes, *args):
        seen.extend(service._user_part(e.digest) for e in episodes)
        return [], {}, "fake"

    monkeypatch.setattr(service, "_extract", extract)
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        rid = _seed(uid, "开头" + "甲" * 7000 + "尾部重要约定", days_ago=0)
        with SessionLocal() as db:
            conv = Conversation(user_id=uid)
            db.add(conv)
            db.flush()
            source = db.get(Run, rid)
            source.conversation_id = conv.id
            msg = QueuedMessage(
                user_id=uid,
                conversation_id=conv.id,
                run_id=rid,
                prompt="已确认插话里的偏好",
                state="steered",
                model="test-pro",
                options={},
                file_ids=[],
            )
            db.add(msg)
            db.flush()
            source.runtime_meta = {"steer_ids": [msg.id]}
            db.commit()
        for index in range(6):
            with SessionLocal() as db:
                row = dream_row(uid)
                row.local_date = f"2026-10-{index + 1:02}"
                db.add(row)
                db.commit()
                did = row.id
            asyncio.run(service.process_dream(did))
        assert "尾部重要约定" in "".join(seen)
        assert "已确认插话里的偏好" in "".join(seen)
        assert "".join(seen) == "开头" + "甲" * 7000 + "尾部重要约定\n\n已确认插话里的偏好"


def test_background_quota_refusal_never_dispatches_a_model(monkeypatch):
    from ailab_platform.quota import quota_windows

    class NoCall:
        async def chat(self, *args, **kw):
            raise AssertionError("quota refusal must happen before network dispatch")

    monkeypatch.setattr(service, "llm_for", lambda model: NoCall())
    monkeypatch.setattr(service.settings, "daily_token_limit", 1)
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        _seed(uid, "需要抽取的长期偏好", days_ago=0)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream(did))
        with SessionLocal() as db:
            assert db.get(MemoryDreamRun, did).status == "failed"
            from ailab_platform.models import ModelUsage

            assert db.query(ModelUsage).filter(ModelUsage.job_id == did).count() == 0
    assert quota_windows()["daily_tokens"][2] == 1


def test_concurrent_dream_dispatch_has_one_model_owner(monkeypatch):
    calls = []

    async def extract(*args):
        calls.append(1)
        await asyncio.sleep(0.05)
        return [], {}, "fake"

    monkeypatch.setattr(service, "_extract", extract)
    with TestClient(app) as c:
        uid = _uid(c, _user(c))
        _seed(uid, "并发认领的记忆", days_ago=0)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id

        async def together():
            await asyncio.gather(service.process_dream(did), service.process_dream(did))

        asyncio.run(together())
        assert calls == [1]
        with SessionLocal() as db:
            assert db.get(MemoryDreamRun, did).status == "applied"


def test_late_historical_fact_cannot_supersede_a_newer_confirmed_state():
    from ailab_platform.memory_recall.policy import model_view

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        newer = put(c, me, [{"content": "用户当前使用 pnpm", "valid_from": "2026-06-01T00:00:00+00:00"}])["entries"][0]
        ep = MemoryEpisode(id="epi_late", observed_at=datetime(2026, 1, 1, tzinfo=UTC))
        rid = _seed(uid, "我使用 npm", days_ago=1)
        with SessionLocal() as db:
            ep.run_id = rid
            service._apply(
                db,
                db.get(User, uid),
                DreamPlan(changes=[DreamChange(op="add", content="用户使用 npm", evidence_run_ids=[rid], supersedes=[newer["id"]])]),
                {rid: ep},
            )
            db.commit()
        doc = c.get("/v1/memory", headers=me).json()
        assert [e["content"] for e in model_view(doc)["entries"]] == ["用户当前使用 pnpm"]
        old = model_view(doc, as_of=datetime(2026, 3, 1, tzinfo=UTC))["entries"]
        assert [e["content"] for e in old] == ["用户使用 npm"]
