"""Filename autocomplete, lineage and visibility contracts on the active test dialect."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text

from ailab_platform.api import file_mentions
from ailab_platform.api.file_mentions import RECENT_CANDIDATES, candidate_query, recent_query
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.file_search import index_pending, index_token, query_fragments, query_tokens
from ailab_platform.models import ApiToken, Artifact, Conversation, FileObject, FileSearch, FileSearchState, Project, Run, new_id
from ailab_platform.storage import get_storage
from tests.test_conversations import _other_user
from tests.test_library import ready

EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture()
def api():
    with TestClient(app) as client:
        client.headers.update(_other_user(client))
        token = client.headers["Authorization"].removeprefix("Bearer ")
        from ailab_platform.db import hash_token

        with SessionLocal() as db:
            client.owner = db.scalar(select(ApiToken.user_id).where(ApiToken.token_hash == hash_token(token)))
        yield client


def add_file(db, owner, name, index=0, **attrs):
    file = FileObject(user_id=owner, filename=name, storage_key=new_id("key"), created_at=EPOCH + timedelta(seconds=index), **attrs)
    if "status" not in attrs:
        file.status = "ready"
    db.add(file)
    db.flush()
    return file


def items(api, q="", **params):
    response = api.get("/v1/files/mentions", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()["items"]


def ids(api, q="", **params):
    return [row["id"] for row in items(api, q, **params)]


def test_mention_scope_precedence_and_artifact_provenance(api):
    owner = api.owner
    with SessionLocal() as db:
        other = db.scalar(select(ApiToken.user_id).where(ApiToken.user_id != owner).limit(1))
        private = Project(user_id=owner, name="私有 A", memory_mode="isolated")
        other_private = Project(user_id=owner, name="私有 B", memory_mode="isolated")
        shared = Project(user_id=owner, name="共享")
        foreign = Project(user_id=other, name="他人的项目")
        db.add_all([private, other_private, shared, foreign])
        db.flush()
        conv = Conversation(user_id=owner, project_id=private.id, title="当前对话")
        source = Conversation(user_id=owner, project_id=shared.id, title="原始交付")
        inaccessible = Conversation(user_id=other, project_id=foreign.id, title="他人的对话")
        db.add_all([conv, source, inaccessible])
        db.flush()
        library = add_file(db, owner, "report-library.txt", 99)
        shared_file = add_file(db, owner, "report-shared.txt", 110, project_id=shared.id)
        local = add_file(db, owner, "report-local.txt", 88, project_id=private.id)
        hidden = add_file(db, owner, "report-hidden.txt", 120, project_id=other_private.id)
        historical = add_file(db, owner, "report-original.txt", 1, is_head=0, project_id=other_private.id)
        attached = add_file(db, owner, "report-attachment.txt", 2, lineage_id=historical.lineage_id, version=2, project_id=other_private.id)
        delivered = add_file(db, owner, "report-delivered.txt", 4, project_id=shared.id)
        add_file(db, owner, "report-pending.txt", 130, status="pending")
        add_file(db, owner, "report-purged.txt", 130, purged_at=EPOCH)
        add_file(db, owner, "report-trashed.txt", 130, deleted_at=EPOCH)
        add_file(db, owner, "report-template.txt", 130, purpose="template")
        add_file(db, other, "report-foreign.txt", 140)
        old_run = Run(user_id=owner, conversation_id=conv.id, prompt="old branch", input_file_ids=[historical.id])
        current = Run(user_id=owner, conversation_id=conv.id, prompt="fork artifact")
        origin = Run(user_id=owner, conversation_id=source.id, prompt="original artifact")
        unattached = Run(user_id=owner, conversation_id=conv.id, prompt="unrelated")
        db.add_all([old_run, current, origin, unattached])
        db.flush()
        conv.active_run_id = unattached.id
        db.add_all(
            [
                Artifact(run_id=origin.id, file_id=delivered.id, created_at=EPOCH),
                Artifact(run_id=current.id, file_id=delivered.id, created_at=EPOCH + timedelta(days=1)),
            ]
        )
        db.commit()

    assert ids(api, "report") == [shared_file.id, library.id, delivered.id]
    assert ids(api, "report", project_id=shared.id) == [shared_file.id, delivered.id, library.id]
    assert ids(api, "report", project_id=private.id) == [local.id]
    assert ids(api, "report", project_id=other_private.id) == [hidden.id, attached.id]
    assert ids(api, conversation_id=conv.id, project_id=shared.id) == [delivered.id, attached.id, local.id]
    results = items(api, "report", conversation_id=conv.id, project_id=shared.id)
    assert [row["id"] for row in results] == [delivered.id, attached.id, local.id]
    assert [row["origin"] for row in results] == ["conversation", "conversation", "project"]
    assert [row["delivered"] for row in results] == [True, False, False]
    assert results[0]["conversation_title"] == "原始交付"
    assert results[1]["project_name"] == "私有 B"
    assert results[2]["conversation_title"] is None
    assert results[0]["download_url"] == f"/v1/files/{delivered.id}/content"
    assert results[1]["versions"] == 2

    for params in (
        {"project_id": foreign.id},
        {"conversation_id": inaccessible.id},
        {"conversation_id": conv.id, "project_id": foreign.id},
        {"project_id": "missing"},
        {"conversation_id": "missing"},
    ):
        assert api.get("/v1/files/mentions", params=params).status_code == 404
    for params in ({"q": "q" * 101}, {"limit": 0}, {"limit": 21}):
        assert api.get("/v1/files/mentions", params=params).status_code == 422


def test_filename_tokens_prefix_and_codepoint_highlight(api):
    with SessionLocal() as db:
        report = add_file(db, api.owner, "🎯QuarterlyReport_v2.pptx")
        chinese = add_file(db, api.owner, "财务季度财报.pdf")
        scrambled = add_file(db, api.owner, "季度-度财.txt")
        unicode = add_file(db, api.owner, "📄ＳtraßeCafe\u0301.txt")
        supplementary = add_file(db, api.owner, "𠮷野.txt")
        db.commit()
    for q, ranges in (
        ("ＲＥＰ ppt", None),  # the first fragment must be whole
        ("report ppt", [[10, 16], [20, 23]]),
        ("QuarterlyReport", [[1, 16]]),
        ("quarterly v2 ppt", [[1, 10], [17, 19], [20, 23]]),
        ("v 2", [[17, 19]]),
        ("port", [[12, 16]]),
    ):
        rows = items(api, q)
        if ranges is None:
            assert rows == []
        else:
            assert [(row["id"], row["highlight"]) for row in rows] == [(report.id, ranges)]
    assert [(row["id"], row["highlight"]) for row in items(api, "财") if row["id"] == chinese.id] == [(chinese.id, [[0, 1], [4, 5]])]
    assert ids(api, "季度财") == [chinese.id]
    assert scrambled.id not in ids(api, "季度财")
    assert ids(api, "𠮷") == [supplementary.id]
    assert [(row["id"], row["highlight"]) for row in items(api, "strasse café")] == [(unicode.id, [[1, 12]])]


def test_sort_compares_match_tier_then_origin_then_recency(api):
    with SessionLocal() as db:
        shared = Project(user_id=api.owner, name="共享")
        db.add(shared)
        db.flush()
        conversation = Conversation(user_id=api.owner, project_id=shared.id, title="对话")
        db.add(conversation)
        db.flush()
        first = add_file(db, api.owner, "report.txt", 1)
        second = add_file(db, api.owner, "draftReport.txt", 2)
        third = add_file(db, api.owner, "old report.txt", 3, project_id=shared.id)
        fourth = add_file(db, api.owner, "new report.txt", 4, project_id=shared.id)
        middle = add_file(db, api.owner, "intellireport.txt", 5, project_id=shared.id)
        db.add(Run(user_id=api.owner, conversation_id=conversation.id, prompt="attached", input_file_ids=[second.id]))
        db.commit()
    assert ids(api, "report", conversation_id=conversation.id) == [first.id, second.id, fourth.id, third.id, middle.id]
    assert ids(api, "report", conversation_id=conversation.id, limit=2) == [first.id, second.id]
    assert ids(api, conversation_id=conversation.id) == [second.id, middle.id, fourth.id, third.id, first.id]


def test_recent_fts_window_keeps_the_newest_page_and_finds_older_rare_hits(api, monkeypatch):
    monkeypatch.setattr(file_mentions, "INDEX_WINDOW", 6)
    monkeypatch.setattr(file_mentions, "INDEX_CANDIDATES", 2)
    with SessionLocal() as db:
        older = add_file(db, api.owner, "ZephyrDraft.txt", 0)
        add_file(db, api.owner, "alphaReport.txt", 1)
        newest = [add_file(db, api.owner, f"recentReport-{i}.txt", i) for i in range(2, 8)]
        # A late insert can have an older timestamp. It belongs outside the exact recent window.
        add_file(db, api.owner, "otherReport.txt", -1)
        db.commit()
        assert file_mentions._index_window(db, api.owner) is not None
    assert ids(api, "report", limit=2) == [newest[-1].id, newest[-2].id]
    assert ids(api, "zephyr", limit=1) == [older.id]


def index_row(lineage_id):
    with SessionLocal() as db:
        return db.scalar(select(FileSearch).where(FileSearch.lineage_id == lineage_id))


def test_index_follows_moves_new_version_trash_restore_and_purge(api):
    initial = ready(api, "OriginalQuarterly.txt")
    initial_id = initial["id"]
    assert index_row(initial_id).file_id == initial_id
    response = api.patch(f"/v1/files/{initial_id}", json={"filename": "RevisedQuarterly.txt"})
    assert response.status_code == 200, response.text
    assert ids(api, "original") == []
    assert ids(api, "revised") == [initial_id]

    project = api.post("/v1/projects", json={"name": "Destination"}).json()
    collision = ready(api, "RevisedQuarterly.txt")
    # The second upload chose a nonconflicting name at root. Give the destination a colliding name before moving.
    response = api.patch(f"/v1/files/{collision['id']}", json={"project_id": project["id"], "filename": "RevisedQuarterly.txt"})
    assert response.status_code == 200, response.text
    moved = api.patch(f"/v1/files/{initial_id}", json={"project_id": project["id"]}).json()
    assert moved["filename"] != "RevisedQuarterly.txt"
    assert index_token("2", "l") in index_row(initial_id).terms.split()

    response = api.post(
        "/v1/files", params={"parent_id": initial_id, "disposition": "copy"}, files={"upload": ("ZephyrVersion.txt", b"v2", "text/plain")}
    )
    assert response.status_code == 201, response.text
    version = response.json()
    assert index_row(initial_id) is None
    assert initial_id not in ids(api, "revised")
    from tests.test_projects import SERVICE

    response = api.patch(f"/internal/files/{version['id']}", headers=SERVICE, json={"status": "ready"})
    assert response.status_code == 200, response.text
    assert ids(api, "zephyr") == [version["id"]]
    assert index_row(initial_id).file_id == version["id"]
    assert get_storage().exists(_storage_key(initial_id))

    restored = api.post(f"/v1/files/{initial_id}/restore-version")
    assert restored.status_code == 201, restored.text
    head = restored.json()
    assert ids(api, "zephyr") == []
    assert head["id"] in ids(api, "revised")
    assert index_row(initial_id).file_id == head["id"]

    response = api.post("/v1/files/batch", json={"ids": [head["id"]], "action": "trash"})
    assert response.status_code == 200, response.text
    assert index_row(initial_id) is None
    assert head["id"] not in ids(api, "revised")
    response = api.post(f"/v1/files/{head['id']}/restore")
    assert response.status_code == 200, response.text
    assert head["id"] in ids(api, "revised")
    assert index_row(initial_id).file_id == head["id"]

    for action in ("trash", "purge"):
        response = api.post("/v1/files/batch", json={"ids": [head["id"]], "action": action})
        assert response.status_code == 200, response.text
    assert index_row(initial_id) is None
    assert head["id"] not in ids(api, "revised")


def _storage_key(file_id):
    with SessionLocal() as db:
        return db.get(FileObject, file_id).storage_key


def test_backfill_is_batched_and_does_not_replace_new_writes(api):
    with SessionLocal() as db:
        watermark = dict(watermark_user_id=api.owner, watermark_created_at=EPOCH, watermark_file_id="zz_mention_legacy0")
        state = db.get(FileSearchState, 1)
        if state is None:
            db.add(FileSearchState(id=1, **watermark))
        else:
            for key, value in watermark.items():
                setattr(state, key, value)
        legacy = []
        for i in range(3):
            fid = f"zz_mention_legacy{i + 1}"
            legacy.append(
                dict(
                    id=fid,
                    user_id=api.owner,
                    lineage_id=fid,
                    filename=f"legacy-file-{i}.txt",
                    storage_key=fid,
                    status="ready",
                    created_at=EPOCH + timedelta(seconds=i + 1),
                )
            )
        db.execute(insert(FileObject), legacy)
        db.commit()
        assert all(index_row(row["id"]) is None for row in legacy)

        assert index_pending(db, batch_size=2)
        assert db.get(FileSearchState, 1).watermark_file_id == legacy[1]["id"]
        assert index_row(legacy[0]["id"]).file_id == legacy[0]["id"]
        assert index_row(legacy[2]["id"]) is None

        live = db.get(FileObject, legacy[2]["id"])
        live.filename = "modern name.txt"
        db.commit()
        terms = index_row(live.lineage_id).terms
        index_pending(db, batch_size=2)
        assert index_row(live.lineage_id).terms == terms
        assert index_token("modern", "l") in terms
        assert index_token("legacy", "l") not in terms


def test_query_plans_use_file_full_text_and_recent_indexes():
    init_db()
    with SessionLocal() as db:
        dialect = db.get_bind().dialect.name
        prefix = "EXPLAIN QUERY PLAN " if dialect == "sqlite" else "EXPLAIN "
        if dialect == "postgresql":
            db.execute(text("SET LOCAL enable_seqscan = off"))
            # Statistics left by other tests must not decide the plan: only an index that yields the order may serve it.
            db.execute(text("SET LOCAL enable_sort = off"))

        def plan(stmt):
            sql = stmt.compile(db.get_bind(), compile_kwargs={"literal_binds": True})
            return " ".join(row[0] if dialect == "postgresql" else row[3] for row in db.execute(text(prefix + str(sql))))

        owner = "mention-plan-owner"
        search = plan(candidate_query(dialect, owner, query_tokens(query_fragments("ra"), owner)))
        if dialect == "sqlite":
            assert "MATERIALIZE matched" in search and "file_search_fts VIRTUAL TABLE INDEX" in search, search
        else:
            assert "ix_file_search_terms" in search, search
        for project in (None, "mention-plan-project"):
            recent = plan(recent_query(owner, project, limit=RECENT_CANDIDATES, project_only=bool(project)))
            assert "ix_files_mentions_" in recent, recent
        references = file_mentions._conversation_lineages(dialect, owner, "mention-plan-conversation")
        conversation = plan(file_mentions.conversation_query(owner, references))
        assert "files_pkey" in conversation if dialect == "postgresql" else "sqlite_autoindex_files_1" in conversation, conversation
        assert "uq_files_lineage_head" in conversation, conversation
