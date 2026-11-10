"""0023 additive migration preserves retries as siblings and checks production query plans."""

from sqlalchemy import func, literal_column, select, text
from sqlalchemy.sql.elements import TextClause

from ailab_platform.conversation_tree import ancestor_query, siblings_query
from ailab_platform.models import Conversation, ConversationSearch, ModelUsage, Run
from ailab_platform.search_index import candidate_query, matching_run_query
from tests.test_migrations import _downgrade_to, _seed_user_and_run, _upgrade_to, assert_schema_matches
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
        # The ORM describes the integrated head, including the later schedules table.
        _upgrade_to(engine, "head")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT id,parent_run_id FROM runs ORDER BY id")).all() == [
                ("r1", None),
                ("r2", "r1"),
                ("r3", "r1"),
                ("r4", "r1"),
                ("r5", "r4"),
            ]
            assert conn.execute(text("SELECT active_run_id FROM conversations WHERE id='c1'")).scalar() == "r5"
            assert_schema_matches(conn)
            if engine.dialect.name == "sqlite":
                assert conn.execute(text("PRAGMA foreign_key_check")).all() == []
        _downgrade_to(engine, "0022")
    _upgrade_to(engine, "0023")


def test_tree_search_pin_context_production_query_plans(engine):
    _upgrade_to(engine, "head")
    path = ancestor_query("c1", "r5")
    queries = {
        "ancestors": select(path.c.id).order_by(path.c.depth.desc()),
        "siblings": siblings_query("c1", "r1").limit(50),
        "search_candidates": candidate_query("u1", ["机器"], engine.dialect.name, limit=50),
        "search_runs": matching_run_query("c1", ["机器"], engine.dialect.name, limit=20),
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
    if engine.dialect.name == "postgresql":
        # Prove the GIN index itself is usable; the candidate query may legitimately prefer the user index.
        tsquery = func.plainto_tsquery(literal_column("'simple'"), "机器")
        queries["search_terms"] = select(ConversationSearch.id).where(ConversationSearch.terms_tsv.op("@@")(tsquery))
    with engine.begin() as conn:
        if engine.dialect.name == "sqlite":
            conn.connection.driver_connection.create_function("ailab_search_lower", 1, lambda value: value.lower() if value else "")
        if engine.dialect.name == "postgresql":
            conn.execute(text("SET LOCAL enable_seqscan=off"))
        for name, query in queries.items():
            prefix = "EXPLAIN QUERY PLAN " if engine.dialect.name == "sqlite" else "EXPLAIN "
            if isinstance(query, TextClause):
                plan = " ".join(
                    str(tuple(row)) for row in conn.execute(text(prefix + str(query)), {"user": "u1", "match": '"机器"', "limit": 50})
                )
            else:
                sql = str(query.compile(engine, compile_kwargs={"literal_binds": True}))
                plan = " ".join(str(tuple(row)) for row in conn.execute(text(prefix + sql)))
            print("TREE PLAN", engine.dialect.name, name, plan)
            assert "INDEX" in plan.upper() or "_pkey" in plan
            if name == "search_candidates" and engine.dialect.name == "sqlite":
                assert "conversation_search_fts" in plan, plan
            if name == "search_terms":
                assert "ix_conversation_search_terms" in plan, plan
            if name == "search_runs":
                assert "ix_runs_conversation_created_id" in plan, plan
            if name == "ancestors" and engine.dialect.name == "sqlite":
                assert plan.count("sqlite_autoindex_runs_1 (id=?)") >= 3, plan
            if name == "ancestors" and engine.dialect.name == "postgresql":
                assert "runs_pkey" in plan and "Join Filter" not in plan, plan
