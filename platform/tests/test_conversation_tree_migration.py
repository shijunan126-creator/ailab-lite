"""0023 additive migration preserves retries as siblings and checks production query plans."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text

from ailab_platform.api.routes_search import conversation_scan, run_scan, search_match_query
from ailab_platform.conversation_tree import ancestor_query, siblings_query
from ailab_platform.models import Base, Conversation, ModelUsage, Run
from tests.test_migrations import _downgrade_to, _seed_user_and_run, _upgrade_to
from tests.test_migrations import engine as engine


def test_tree_backfill_and_roundtrip(engine):
    _upgrade_to(engine, "0022")
    with engine.begin() as conn:
        _seed_user_and_run(conn)
        conn.execute(
            text(
                "INSERT INTO conversations(id,user_id,title,model,created_at,updated_at) "
                "VALUES ('c1','u1','title','m','2026-01-01','2026-01-01')"
            )
        )
        conn.execute(text("UPDATE runs SET conversation_id='c1' WHERE id='r1'"))
        for id_, retry, superseded in (("r2", None, "r3"), ("r3", "r2", "r4"), ("r4", "r3", None), ("r5", None, None)):
            conn.execute(
                text(
                    "INSERT INTO runs(id,user_id,conversation_id,title,prompt,status,model,input_file_ids,result,summary,error,"
                    "created_at,updated_at,retry_of,superseded_by_run_id) "
                    "VALUES (:id,'u1','c1','t','p','succeeded','m','[]','{}','','',:created,:created,:retry,:superseded)"
                ),
                {"id": id_, "created": f"2026-01-0{id_[-1]}", "retry": retry, "superseded": superseded},
            )
    for _ in range(2):
        _upgrade_to(engine, "0023")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT id,parent_run_id FROM runs ORDER BY id")).all() == [
                ("r1", None),
                ("r2", "r1"),
                ("r3", "r1"),
                ("r4", "r1"),
                ("r5", "r4"),
            ]
            assert conn.execute(text("SELECT active_run_id FROM conversations WHERE id='c1'")).scalar() == "r5"
            assert compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata) == []
            if engine.dialect.name == "sqlite":
                assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        _downgrade_to(engine, "0022")
    _upgrade_to(engine, "0023")


def test_tree_search_pin_context_production_query_plans(engine):
    _upgrade_to(engine, "head")
    path = ancestor_query("c1", "r5")
    window = run_scan("c1")
    queries = {
        "ancestors": select(path.c.id).order_by(path.c.depth.desc()),
        "siblings": siblings_query("c1", "r1").limit(50),
        "search_conversations": conversation_scan("u1"),
        "search_runs": search_match_query(window, "literal_", engine.dialect.name),
        "pin": select(Conversation.id)
        .where(Conversation.user_id == "u1", Conversation.deleted_at.is_(None), Conversation.project_id.is_(None))
        .order_by(Conversation.pinned_at.desc().nulls_last(), Conversation.updated_at.desc())
        .limit(100),
        "context": select(ModelUsage.prompt_tokens)
        .where(ModelUsage.run_id == "r5", ModelUsage.quiet.is_(False), ModelUsage.status == "settled")
        .order_by(ModelUsage.created_at.desc(), ModelUsage.id.desc())
        .limit(1),
        "newest_child": select(Run.id)
        .where(Run.conversation_id == "c1", Run.parent_run_id == "r1")
        .order_by(Run.created_at.desc(), Run.id.desc())
        .limit(1),
    }
    with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            conn.connection.driver_connection.create_function("ailab_search_lower", 1, lambda value: value.lower() if value else "")
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL enable_seqscan=off"))
        for name, query in queries.items():
            sql = str(query.compile(engine, compile_kwargs={"literal_binds": True}))
            prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
            plan = " ".join(str(tuple(r)) for r in conn.execute(text(prefix + sql)))
            print("TREE PLAN", engine.dialect.name, name, plan)
            assert "INDEX" in plan.upper() or "_pkey" in plan
