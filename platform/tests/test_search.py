"""Whole-account conversation search: ownership, exact text, ranking and the index catch-up."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal, engine
from ailab_platform.deps import get_db
from ailab_platform.models import Conversation, ConversationSearch, Run, SearchIndexState
from ailab_platform.search_index import index_conversation, index_pending
from tests.test_chat_fork_retry import create
from tests.test_conversations import AUTH, _finish, _other_user


def search(c, query, limit=20):
    response = c.get("/v1/search", headers=AUTH, params={"q": query, "limit": limit})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "next_cursor" not in body
    assert len(body["items"]) <= limit
    return body["items"]


def test_search_title_newest_branch_match_highlights_escaping_and_ownership():
    with TestClient(app) as c:
        needle = "Unique_100%\\完成"
        conv, turns = create(c, ("chat", "chat"))
        _root, old = [r["id"] for r in turns]
        with SessionLocal() as db:
            db.get(Run, old).summary = "前文" * 100 + needle + "后文" * 100
            db.commit()
        edited = c.post(f"/v1/runs/{old}/edit", headers=AUTH, json={"prompt": "different path"}).json()
        _finish(c, edited["id"], "no hit")
        item = search(c, needle.lower(), limit=1)[0]
        assert item["conversation_id"] == conv["id"] and item["run_id"] == old and item["match"] == "answer"
        assert len(item["snippet"]) <= 120
        assert item["highlight"] and item["snippet"][slice(*item["highlight"][0])].lower() == needle.lower()
        other = _other_user(c)
        assert c.get("/v1/search", headers=other, params={"q": needle}).json()["items"] == []
        # A title outranks the answer in the same conversation, and project conversations are included.
        project = c.post("/v1/projects", headers=AUTH, json={"name": "search project"}).json()
        c.patch(f"/v1/conversations/{conv['id']}", headers=AUTH, json={"title": needle, "project_id": project["id"]})
        item = search(c, needle)[0]
        assert item["match"] == "title" and item["run_id"] is None and item["project_id"] == project["id"]
        assert item["snippet"] == needle
        assert item["snippet"][slice(*item["highlight"][0])].lower() == needle.lower()
        c.delete(f"/v1/conversations/{conv['id']}", headers=AUTH)
        assert search(c, needle) == []
        # Literal percent/underscore/backslash never broaden into a wildcard search.
        no = c.post("/v1/conversations", headers=AUTH, json={"title": "uniquex100anything完成"}).json()
        assert all(item["conversation_id"] != no["id"] for item in search(c, needle))


def test_search_finds_a_deep_hit_in_one_request():
    with TestClient(app) as c:
        conv, turns = create(c)
        root = turns[0]["id"]
        needle = "deep-search-unique"
        with SessionLocal() as db:
            original = db.get(Run, root)
            original.prompt = needle
            db.add_all(
                [Run(user_id=original.user_id, conversation_id=conv["id"], prompt="no match", status="succeeded") for _ in range(1100)]
            )
            db.commit()
        # No continuation and no recent-window cutoff: the old root is found in the same request.
        items = search(c, needle)
        assert [item["run_id"] for item in items] == [root]
        for params in ({"q": ""}, {"q": "   "}, {"q": "a" * 201}, {"q": "x", "limit": 51}, {"q": "x", "limit": 0}):
            assert c.get("/v1/search", headers=AUTH, params=params).status_code == 422


def test_search_best_match_uses_newest_run_prompt_before_answer():
    with TestClient(app) as c:
        conv, turns = create(c, ("chat", "chat"))
        needle = "newest-search-unique"
        with SessionLocal() as db:
            for run in db.scalars(select(Run).where(Run.conversation_id == conv["id"])):
                run.prompt = needle
                run.summary = needle
            db.commit()
        item = search(c, needle)[0]
        assert item["run_id"] == turns[-1]["id"] and item["match"] == "prompt"


def test_search_unicode_case_insensitive():
    with TestClient(app) as c:
        conv, turns = create(c)
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).summary = "ÉCOLE 亚洲"
            db.commit()
        item = search(c, "école 亚洲")[0]
        assert item["conversation_id"] == conv["id"] and item["match"] == "answer"
        assert item["highlight"] == [[0, 5], [6, 8]]
        assert "".join(item["snippet"][slice(*span)] for span in item["highlight"]) == "ÉCOLE亚洲"


def test_search_highlights_query_longer_than_snippet():
    with TestClient(app) as c:
        conv, turns = create(c)
        needle = "z" * 200
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).prompt = needle
            db.commit()
        item = search(c, needle)[0]
        assert item["conversation_id"] == conv["id"] and len(item["snippet"]) == 120
        assert item["highlight"] == [[0, 120]]


def test_search_requires_every_term_and_reads_across_prompt_and_answer():
    with TestClient(app) as c:
        first, turns = create(c)
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).prompt = "模型训练"
            db.get(Run, turns[0]["id"]).summary = "部署完成"
            db.commit()
        second, other = create(c)
        with SessionLocal() as db:
            db.get(Run, other[0]["id"]).prompt = "模型评估"
            db.get(Run, other[0]["id"]).summary = "只有评估"
            db.commit()
        items = search(c, "模型 训练")
        assert [item["conversation_id"] for item in items] == [first["id"]]
        assert items[0]["match"] == "prompt"
        # One term in the question and one in the answer still describe the same turn.
        items = search(c, "模型 训练 部署")
        assert [item["conversation_id"] for item in items] == [first["id"]]
        assert search(c, "模型 不存在的词") == []
        assert all(item["conversation_id"] != second["id"] for item in search(c, "模型 训练"))


def test_search_latin_words_and_identifiers():
    with TestClient(app) as c:
        conv, turns = create(c)
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).summary = "Deploy the ailab_platform service to production"
            db.commit()
        for query in ("DEPLOY", "platform", "ailab_platform"):
            item = search(c, query)[0]
            assert item["conversation_id"] == conv["id"] and item["match"] == "answer"


def test_search_a_title_only_conversation():
    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={"title": "季度计划与预算"}).json()
        item = search(c, "季度 预算")[0]
        assert item["conversation_id"] == conv["id"] and item["match"] == "title" and item["run_id"] is None
        assert item["highlight"] == [[0, 2], [5, 7]]


def test_search_highlights_a_long_title_at_its_real_offset():
    with TestClient(app) as c:
        title = "前" * 150 + "长标题标记"
        conv = c.post("/v1/conversations", headers=AUTH, json={"title": title}).json()
        item = search(c, "长标题标记")[0]
        assert item["conversation_id"] == conv["id"] and item["match"] == "title"
        assert item["snippet"] == title
        assert item["snippet"][slice(*item["highlight"][0])] == "长标题标记"


def test_search_indexes_an_auto_generated_title():
    with TestClient(app) as c:
        conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "no title term here"}).json()
        _finish(c, run["id"], "answer", conversation_title="自动标题唯一词")
        item = search(c, "自动标题唯一词")[0]
        assert item["conversation_id"] == conv["id"] and item["match"] == "title"
        assert item["run_id"] is None


def test_search_single_character_uses_the_recent_fallback():
    with TestClient(app) as c:
        conv, turns = create(c)
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).summary = "鑫是很少见的字"
            db.commit()
        item = search(c, "鑫")[0]
        assert item["conversation_id"] == conv["id"] and item["match"] == "answer"


def test_search_index_catch_up_repairs_missing_rows():
    with TestClient(app) as c:
        conv, turns = create(c)
        needle = "backfill-search-unique"
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).summary = needle
            db.commit()
            db.execute(delete(ConversationSearch))
            db.execute(delete(SearchIndexState))
            db.commit()
            while index_pending(db):
                pass
        item = search(c, needle)[0]
        assert item["conversation_id"] == conv["id"] and item["match"] == "answer"


@pytest.mark.parametrize(("source", "query"), [("ＳＥＲＶＩＣＥ", "service"), ("Straße", "STRASSE"), ("cafe\u0301", "café")])
def test_search_uses_index_normalization_for_exact_matches(source, query):
    with TestClient(app) as c:
        conv, turns = create(c)
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).summary = source
            db.commit()
        item = search(c, query)[0]
        assert item["conversation_id"] == conv["id"]
        assert item["highlight"] == [[0, len(source)]]


def test_search_does_not_match_hidden_ui_source():
    with TestClient(app) as c:
        conv, turns = create(c)
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).summary = 'visible text\n```openui\nroot = Text("hiddenuniqueneedle")\n```'
            db.commit()
        assert search(c, "hiddenuniqueneedle") == []
        assert search(c, "visible")[0]["conversation_id"] == conv["id"]


def test_search_latin_word_boundaries_are_checked_in_the_matching_run():
    with TestClient(app) as c:
        conv, turns = create(c, ("chat", "chat"))
        needle = "wordboundaryneedle"
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).prompt = needle
            db.get(Run, turns[1]["id"]).prompt = needle + "suffix"
            db.commit()
        item = search(c, needle)[0]
        assert item["conversation_id"] == conv["id"] and item["run_id"] == turns[0]["id"]


def test_search_indexes_runs_whose_conversation_is_in_the_same_flush():
    with TestClient(app) as c:
        existing, _ = create(c)
        with SessionLocal() as db:
            user_id = db.get(Conversation, existing["id"]).user_id
            conv = Conversation(id="cnv_pending_search", user_id=user_id, title="")
            db.add_all([conv, Run(user_id=user_id, conversation_id=conv.id, prompt="pendinguniqueneedle")])
            db.commit()
        assert search(c, "pendinguniqueneedle")[0]["conversation_id"] == conv.id


def test_search_index_union_refreshes_stale_session_state():
    with TestClient(app) as c:
        conv, _ = create(c)
        with SessionLocal() as stale, SessionLocal() as writer:
            stale_row = stale.scalar(select(ConversationSearch).where(ConversationSearch.conversation_id == conv["id"]))
            # Keep the old ORM value alive across another transaction's committed index update.
            index_conversation(writer, writer.get(Conversation, conv["id"]), ["firstuniqueneedle"])
            writer.commit()
            index_conversation(stale, stale.get(Conversation, conv["id"]), ["seconduniqueneedle"])
            stale.commit()
            stale.refresh(stale_row)
            assert {"firstuniqueneedle", "seconduniqueneedle"} <= set(stale_row.terms.split())


def test_search_scans_past_false_positive_candidate_pages(monkeypatch):
    from ailab_platform import search_index

    monkeypatch.setattr(search_index, "CANDIDATE_PAGE", 50)
    with TestClient(app) as c:
        true_conv, turns = create(c)
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).prompt = "目标词组"
            db.commit()
        with SessionLocal() as db:
            user_id = db.get(Conversation, true_conv["id"]).user_id
            # The same bigrams, but the literal phrase never appears. More than the old 2,000-candidate cutoff.
            db.add_all([Conversation(user_id=user_id, title="目标 标词 词组") for _ in range(2001)])
            db.commit()
        run_queries = []
        clock = SimpleNamespace(now=0.0)
        monkeypatch.setattr(search_index, "time", SimpleNamespace(monotonic=lambda: clock.now))

        def search_db():
            with SessionLocal() as db:
                db.connection(execution_options={"search_regression": True})
                yield db

        monkeypatch.setitem(app.dependency_overrides, get_db, search_db)

        def count_run_queries(_conn, _cursor, statement, _params, context, _executemany):
            if context.execution_options.get("search_regression") and "FROM runs" in statement:
                run_queries.append(statement)
                # Model 5ms per database round trip deterministically. Do not sleep, extend the product deadline,
                # or rely on the local database being as fast as an unloaded laptop.
                clock.now += 0.005

        event.listen(engine, "before_cursor_execute", count_run_queries)
        try:
            # Background jobs share this engine, but their connections are outside the request being measured.
            with SessionLocal() as unrelated:
                unrelated.execute(select(Run.id).limit(1)).all()
            assert not run_queries
            assert search(c, "目标词组")[0]["conversation_id"] == true_conv["id"]
        finally:
            event.remove(engine, "before_cursor_execute", count_run_queries)
        assert 0 < len(run_queries) <= 42  # 41 candidate pages and one real conversation's matching-run query.


def test_search_scans_past_hidden_run_matches():
    with TestClient(app) as c:
        conv, turns = create(c)
        needle = "hiddenrunbudgetneedle"
        with SessionLocal() as db:
            root = db.get(Run, turns[0]["id"])
            root.prompt = needle
            db.add_all(
                [
                    Run(
                        user_id=root.user_id,
                        conversation_id=conv["id"],
                        prompt="no match",
                        summary=f'```openui\nroot = Text("{needle}")\n```',
                    )
                    for _ in range(201)
                ]
            )
            db.commit()
        assert search(c, needle)[0]["run_id"] == turns[0]["id"]


@pytest.mark.parametrize("missing_row", [False, True])
def test_search_index_concurrent_writers_keep_both_term_sets(missing_row):
    with TestClient(app) as c:
        conv, _ = create(c)
        if missing_row:
            with SessionLocal() as db:
                db.execute(delete(ConversationSearch).where(ConversationSearch.conversation_id == conv["id"]))
                db.commit()
        barrier = Barrier(2)

        def write(needle):
            with SessionLocal() as db:
                row = db.scalar(select(ConversationSearch).where(ConversationSearch.conversation_id == conv["id"]))
                assert (row is None) == missing_row
                conversation = db.get(Conversation, conv["id"])
                barrier.wait(timeout=10)
                index_conversation(db, conversation, [needle])
                db.commit()

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(write, ["concurrentfirstneedle", "concurrentsecondneedle"]))
        with SessionLocal() as db:
            row = db.scalar(select(ConversationSearch).where(ConversationSearch.conversation_id == conv["id"]))
            assert {"concurrentfirstneedle", "concurrentsecondneedle"} <= set(row.terms.split())


def test_search_timeout_is_an_error_instead_of_a_false_empty_result(monkeypatch):
    from ailab_platform import search_index

    monkeypatch.setattr(search_index, "SEARCH_TIMEOUT_SECONDS", 0)
    with TestClient(app) as c:
        response = c.get("/v1/search", headers=AUTH, params={"q": "timeoutneedle"})
        assert response.status_code == 503
        assert "搜索超时" in response.json()["detail"]
