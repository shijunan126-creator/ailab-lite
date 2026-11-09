"""First-chunk preceding context, evidence isolation and indexed deletion propagation."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.memory_dream import service
from ailab_platform.models import MemoryEpisode, MemoryWorkItem, Run, User, utcnow
from tests.test_memory_hardening import dream_row
from tests.test_memory_recall import _conversation, _seed, _uid
from tests.test_skill_settings import _user


def test_confirmation_uses_preceding_meaning_but_only_this_turn_as_evidence(monkeypatch):
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid)
        previous = _seed(uid, "数据库选哪个？", "建议用PostgreSQL", days_ago=2, conversation_id=conv)
        current = _seed(uid, "行，就这么定了", days_ago=1, conversation_id=conv)
        with SessionLocal() as db:
            db.add(MemoryWorkItem(run_id=previous, user_id=uid, state="done"))
            db.commit()
            owner = db.get(User, uid)
            run = db.get(Run, current)
            first = service._episodes(db, owner, [run])[0]
            assert first.id == service._episodes(db, owner, [run])[0].id
            data = json.loads(first.digest)
            assert "PostgreSQL" in data["context"] and len(data["context"]) <= 300
            assert first.context_run_id == previous
            db.commit()

        async def chat(messages, *args):
            ep = json.loads(messages[1]["content"])[0]
            assert ep["run_id"] == current and "PostgreSQL" in ep["context"]
            return json.dumps(
                {"ops": [{"op": "add", "kind": "project", "content": "项目采用PostgreSQL", "quote": "行，就这么定了", "run": current}]}
            ), {}

        monkeypatch.setattr(service, "_chat", chat)
        with SessionLocal() as db:
            row = dream_row(uid)
            db.add(row)
            db.commit()
            did = row.id
        asyncio.run(service.process_dream_chain(did))
        entries = c.get("/v1/memory", headers=me).json()["entries"]
        assert len(entries) == 1 and entries[0]["evidence_run_ids"] == [current]
        from ailab_platform.memory_dream.document import apply_ops

        with SessionLocal() as db:
            owner = db.get(User, uid)
            ep = db.scalar(select(MemoryEpisode).where(MemoryEpisode.run_id == current))
            assert not apply_ops(
                [],
                [],
                [ep],
                [{"op": "add", "kind": "project", "content": "上下文伪证据", "quote": "建议用PostgreSQL", "run": current}],
                owner,
                None,
            )[1]


def test_context_skips_superseded_and_excluded_and_only_first_chunk():
    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid)
        valid = _seed(uid, "原始用户尾部", "有效建议", days_ago=4, conversation_id=conv)
        replaced = _seed(uid, "废弃轮次", "废弃建议", days_ago=3, conversation_id=conv)
        excluded = _seed(uid, "测试轮次", "测试建议", days_ago=2, conversation_id=conv)
        current = _seed(uid, "确认" + "长" * 5000, days_ago=1, conversation_id=conv)
        with SessionLocal() as db:
            db.get(Run, replaced).superseded_by_run_id = current
            db.add(MemoryWorkItem(run_id=excluded, user_id=uid, state="skipped"))
            db.flush()
            owner = db.get(User, uid)
            first = service._episodes(db, owner, [db.get(Run, current)])[0]
            assert first.context_run_id == valid and "有效建议" in first.digest and "废弃建议" not in first.digest
            db.add(MemoryWorkItem(run_id=current, user_id=uid, source_offset=4000))
            db.flush()
            later = service._episodes(db, owner, [db.get(Run, current)])[0]
            assert "context" not in json.loads(later.digest) and later.context_run_id is None
            assert "context" not in json.loads(service._episodes(db, owner, [db.get(Run, valid)])[0].digest)
            db.rollback()


def test_deleted_context_is_redacted_and_not_included_in_new_episodes():
    from ailab_platform.memory_recall.lineage import purge_episodes_for_runs

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid)
        prev = _seed(uid, "私密原话", "CONTEXT_SECRET_TEXT", days_ago=2, conversation_id=conv)
        current = _seed(uid, "确认", days_ago=1, conversation_id=conv)
        with SessionLocal() as db:
            owner = db.get(User, uid)
            ep = service._episodes(db, owner, [db.get(Run, current)])[0]
            db.commit()
            eid = ep.id
            purge_episodes_for_runs(db, [prev])
            db.commit()
            assert "CONTEXT_SECRET_TEXT" not in db.get(MemoryEpisode, eid).digest
            assert db.get(MemoryEpisode, eid).context_run_id is None
            db.get(Run, prev).superseded_by_run_id = current
            db.commit()
            new = service._episodes(db, owner, [db.get(Run, current)])[0]
            assert "context" not in json.loads(new.digest)
        assert c.delete(f"/v1/conversations/{conv}", headers=me).status_code == 204


@pytest.mark.parametrize("exclusion", ["eval", "evaluation", "write_disabled", "invalid", "deleted"])
def test_excluded_previous_turn_never_reaches_context(exclusion):
    from ailab_platform.memory_dream.service import _episode_context

    with TestClient(app) as c:
        me = _user(c)
        uid = _uid(c, me)
        conv = _conversation(uid)
        prev = _seed(uid, "excluded user", "excluded reply", days_ago=2, conversation_id=conv)
        current = _seed(uid, "确认", days_ago=1, conversation_id=conv)
        with SessionLocal() as db:
            source = db.get(Run, prev)
            if exclusion in {"eval", "evaluation"}:
                source.options = {exclusion: True}
            elif exclusion == "write_disabled":
                source.options = {"memory_policy": {"write": False}}
            elif exclusion == "deleted":
                from ailab_platform.models import Conversation

                db.get(Conversation, conv).deleted_at = utcnow()
            else:
                source.superseded_by_run_id = current
            db.flush()
            assert _episode_context(db, db.get(Run, current)) == {}
            db.rollback()
