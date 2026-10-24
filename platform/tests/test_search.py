"""Bounded all-conversation search: escaping, ownership, branch retention and continuation."""

from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import Run
from tests.test_chat_fork_retry import create
from tests.test_conversations import AUTH, _finish, _other_user


def collect(c, query, limit=20):
    cursor, results, cursors = None, [], set()
    for _ in range(100):
        response = c.get("/v1/search", headers=AUTH, params={"q": query, "limit": limit, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= limit
        results.extend(body["items"])
        cursor = body["next_cursor"]
        if not cursor:
            return results
        assert cursor not in cursors
        cursors.add(cursor)
    raise AssertionError("search did not finish")


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
        results = collect(c, needle.lower(), limit=1)
        assert len(results) == 1
        item = results[0]
        assert item["conversation_id"] == conv["id"] and item["run_id"] == old and item["match"] == "answer"
        assert len(item["snippet"]) <= 120
        assert item["highlight"] and item["snippet"][slice(*item["highlight"][0])].lower() == needle.lower()
        other = _other_user(c)
        assert c.get("/v1/search", headers=other, params={"q": needle}).json()["items"] == []
        # A title outranks every matching run, and the search includes project conversations.
        project = c.post("/v1/projects", headers=AUTH, json={"name": "search project"}).json()
        c.patch(f"/v1/conversations/{conv['id']}", headers=AUTH, json={"title": needle, "project_id": project["id"]})
        item = collect(c, needle)[0]
        assert item["match"] == "title" and item["run_id"] is None and item["project_id"] == project["id"]
        c.delete(f"/v1/conversations/{conv['id']}", headers=AUTH)
        assert collect(c, needle) == []
        # Literal percent/underscore/backslash never broaden into a wildcard search.
        no = c.post("/v1/conversations", headers=AUTH, json={"title": "uniquex100anything完成"}).json()
        assert all(item["conversation_id"] != no["id"] for item in collect(c, needle))


def test_search_bounded_scan_continues_large_branch_set_and_limits():
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
        first = c.get("/v1/search", headers=AUTH, params={"q": needle}).json()
        assert first["items"] == [] and first["next_cursor"]
        assert collect(c, needle)[0]["run_id"] == root
        cursor = first["next_cursor"]
        assert c.get("/v1/search", headers=AUTH, params={"q": "changed", "cursor": cursor}).status_code == 422
        assert c.get("/v1/search", headers=_other_user(c), params={"q": needle, "cursor": cursor}).status_code == 422
        for params in ({"q": ""}, {"q": "a" * 201}, {"q": "x", "limit": 51}, {"q": "x", "limit": 0}, {"q": "x", "cursor": "bogus"}):
            assert c.get("/v1/search", headers=AUTH, params=params).status_code == 422


def test_search_best_match_uses_newest_run_prompt_before_answer():
    with TestClient(app) as c:
        conv, turns = create(c, ("chat", "chat"))
        needle = "newest-search-unique"
        with SessionLocal() as db:
            for r in db.scalars(select(Run).where(Run.conversation_id == conv["id"])):
                r.prompt = needle
                r.summary = needle
            db.commit()
        item = collect(c, needle)[0]
        assert item["run_id"] == turns[-1]["id"] and item["match"] == "prompt"


def test_search_unicode_case_insensitive():
    with TestClient(app) as c:
        conv, turns = create(c)
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).summary = "ÉCOLE 亚洲"
            db.commit()
        item = collect(c, "école 亚洲")[0]
        assert item["conversation_id"] == conv["id"] and item["highlight"] == [[0, 8]]


def test_search_highlights_query_longer_than_snippet():
    with TestClient(app) as c:
        conv, turns = create(c)
        needle = "z" * 200
        with SessionLocal() as db:
            db.get(Run, turns[0]["id"]).prompt = needle
            db.commit()
        item = collect(c, needle)[0]
        assert item["conversation_id"] == conv["id"] and len(item["snippet"]) == 120
        assert item["highlight"] == [[0, 120]]
