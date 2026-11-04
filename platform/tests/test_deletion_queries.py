"""Deletion visibility and draft-source lookup use the existing indexes on both databases."""

import json

from sqlalchemy import insert, select, text
from sqlalchemy.orm import Session

from ailab_platform.migrate import run_migrations
from ailab_platform.models import Conversation, Run, SkillDraft, User
from ailab_platform.run_visibility import live_run_predicate
from tests.test_migrations import engine as engine


def test_visibility_and_draft_source_queries_are_indexed(engine):
    run_migrations(engine)
    with Session(engine) as db:
        db.add_all([User(id=f"user-{i}", email=f"{i}@test.invalid") for i in range(100)])
        db.flush()
        db.execute(insert(Conversation), [dict(id=f"conv-{i}", user_id=f"user-{i % 100}") for i in range(2000)])
        db.execute(
            insert(Run),
            [dict(id=f"run-{i}", user_id=f"user-{i % 100}", conversation_id=f"conv-{i}", prompt="p", model="m") for i in range(2000)],
        )
        db.execute(
            insert(SkillDraft),
            [
                dict(user_id=f"user-{i % 100}", origin="skill", action="update", status="ready", base_file_id=f"file-{i}")
                for i in range(2000)
            ],
        )
        db.commit()
        for table in ("runs", "conversations", "skill_drafts"):
            db.execute(text(f"ANALYZE {table}"))
        queries = [
            (
                select(Run.id).where(Run.user_id == "user-1", live_run_predicate()).order_by(Run.created_at.desc()).limit(50),
                "runs",
                "user_id",
            ),
            (
                select(SkillDraft.id).where(
                    SkillDraft.user_id == "user-1",
                    SkillDraft.base_file_id.in_(["file-1"]),
                    SkillDraft.status.in_(["ready", "preparing", "failed"]),
                ),
                "skill_drafts",
                "base_file_id",
            ),
        ]
        for query, table, column in queries:
            sql = str(query.compile(engine, compile_kwargs={"literal_binds": True}))
            prefix = "EXPLAIN (ANALYZE, FORMAT JSON) " if engine.dialect.name == "postgresql" else "EXPLAIN QUERY PLAN "
            result = db.execute(text(prefix + sql))
            if engine.dialect.name == "postgresql":
                plan = result.scalar_one()[0]["Plan"]

                def nodes(node):
                    yield node
                    for child in node.get("Plans", []):
                        yield from nodes(child)

                assert any(
                    node.get("Relation Name") == table and column in str(node.get("Index Cond", node.get("Recheck Cond", "")))
                    for node in nodes(plan)
                ), json.dumps(plan)
            else:
                plan = [row[3] for row in result]
                assert any(f"SEARCH {table} USING " in row and f"{column}=?" in row for row in plan), plan
            assert db.scalars(query).all()
