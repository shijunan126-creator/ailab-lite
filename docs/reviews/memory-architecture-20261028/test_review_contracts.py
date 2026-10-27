"""Review-only contract probes. Expected failures document main@0659101; no product edits.

Run from platform with PYTHONPATH=.: python -m pytest
../docs/reviews/memory-architecture-20261028/test_review_contracts.py --tb=short
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import tests.conftest  # noqa: F401 -- isolate settings before importing the application
from ailab_platform.api.main import app
from ailab_platform.api.routes_memory import memory_for
from ailab_platform.contracts.memory import DreamChange, DreamPlan
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.memory_recall import embeddings
from ailab_platform.models import MemoryDreamRun, MemoryEpisode, Run, User
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
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
        fitted = service._fit_episodes(
            episodes, service.settings.memory_dream_input_chars
        )
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
        ids = [
            _seed(uid, str(i) + "甲" * 3999, "乙" * 2000, days_ago=0) for i in range(3)
        ]
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream(did))
        with SessionLocal() as db:
            last = service._last_success(db, uid)
            assert last is not None and last.status == "applied"
            eligible = {
                r.id for r in service._new_runs(db, uid, last, datetime.now(UTC))
            }
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
            service._apply(
                db,
                db.get(User, uid),
                DreamPlan(changes=[DreamChange(op="add", content=content)]),
                {},
            )
            db.commit()
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
    assert not out or (
        out[0]["evidence_run_ids"] == ["run_a"]
        and out[0]["evidence_episode_ids"] == ["epi_a"]
    ), out


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
            last = dream_row(
                uid, cursor_run_id=newer, finished_at=now - timedelta(hours=1)
            )
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
    monkeypatch.setattr(
        embeddings.settings, "memory_embedding_base_url", "http://unused.invalid/v1"
    )
    monkeypatch.setattr(embeddings.settings, "memory_embedding_model", "review-model")
    monkeypatch.setattr(
        embeddings, "embed", lambda texts, **kw: [[1.0, 0.0] for _ in texts]
    )
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
        assert (
            embeddings.index_pending(user_id=good_uid) == 1
        )  # endpoint and candidate work
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
