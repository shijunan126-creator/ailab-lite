"""Branch fences survive stale tabs, retries and summary source revalidation."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from ailab_platform.api.main import app
from ailab_platform.contracts.context_summary import ContextSummary, SummaryEntry, SummarySource
from ailab_platform.db import SessionLocal
from ailab_platform.models import ContextSummaryFrame, Conversation, FileObject, MessageFileRef, Run
from ailab_platform.storage import get_storage
from tests.run_claims import SERVICE, attempt_headers
from tests.test_chat_fork_retry import create
from tests.test_conversations import AUTH, _finish


def test_branch_fence_rejects_stale_actions_but_preserves_send_replay_and_continuation():
    with TestClient(app) as c:
        conv, turns = create(c, ("chat", "chat"))
        root, old = [r["id"] for r in turns]
        body = {"prompt": "original send", "mode": "chat", "client_send_id": "branch-fenced-send", "expected_branch_revision": 0}
        sent = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json=body)
        assert sent.status_code == 201, sent.text
        _finish(c, sent.json()["id"], "done")
        assert c.get(f"/v1/conversations/{conv['id']}", headers=AUTH).json()["branch_revision"] == 0
        edited = c.post(f"/v1/runs/{old}/edit", headers=AUTH, json={"prompt": "new branch", "expected_branch_revision": 0})
        assert edited.status_code == 201, edited.text
        assert edited.json()["branch_revision"] == 1 and edited.json()["parent_run_id"] == root
        _finish(c, edited.json()["id"], "new answer")
        # Dedupe precedes the fence: a lost original response is still safely recoverable.
        assert c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json=body).json()["id"] == sent.json()["id"]
        for path, action in [
            (f"/v1/conversations/{conv['id']}/messages", {"prompt": "stale"}),
            (f"/v1/conversations/{conv['id']}/queue", {"prompt": "stale"}),
            (f"/v1/conversations/{conv['id']}/active", {"run_id": old}),
            (f"/v1/runs/{root}/edit", {"prompt": "stale edit"}),
            (f"/v1/runs/{edited.json()['id']}/retry", {}),
        ]:
            response = c.post(path, headers=AUTH, json={**action, "expected_branch_revision": 0})
            assert response.status_code == 409, response.text
            assert response.json()["detail"]["code"] == "conversation_branch_changed"
        selected = c.post(
            f"/v1/conversations/{conv['id']}/active", headers=AUTH, json={"run_id": old, "expected_branch_revision": 1}
        ).json()
        assert selected["branch_revision"] == 2
        assert selected["active_run_id"] == sent.json()["id"]
        # Switching to the same selected path does not invalidate uploads on that path.
        same = c.post(f"/v1/conversations/{conv['id']}/active", headers=AUTH, json={"run_id": old, "expected_branch_revision": 2}).json()
        assert same["branch_revision"] == 2
        continued = c.post(
            f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "continue", "expected_branch_revision": 2}
        )
        assert continued.status_code == 201, continued.text
        assert continued.json()["parent_run_id"] == sent.json()["id"]


def test_queued_replay_remains_idempotent_after_branch_switch():
    with TestClient(app) as c:
        conv, _turns = create(c)
        active = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "busy"}).json()
        body = {"prompt": "queued", "client_send_id": "fenced-queue", "expected_branch_revision": 0}
        queued = c.post(f"/v1/conversations/{conv['id']}/queue", headers=AUTH, json=body)
        assert queued.status_code == 201, queued.text
        c.post(f"/v1/runs/{active['id']}/cancel", headers=AUTH)
        retry = c.post(f"/v1/runs/{active['id']}/retry", headers=AUTH, json={"expected_branch_revision": 0})
        assert retry.status_code == 201, retry.text
        assert c.post(f"/v1/conversations/{conv['id']}/queue", headers=AUTH, json=body).json()["id"] == queued.json()["id"]


@pytest.mark.parametrize("action", ["edit", "switch"])
def test_new_summary_never_inherits_abandoned_frame_revalidation(action):
    with TestClient(app) as c:
        conv, turns = create(c, ("chat", "chat"))
        root, old = [r["id"] for r in turns]
        if action == "switch":
            replacement = c.post(f"/v1/runs/{old}/edit", headers=AUTH, json={"prompt": "new branch"}).json()
            _finish(c, replacement["id"], "new")
            c.post(f"/v1/conversations/{conv['id']}/active", headers=AUTH, json={"run_id": old})
        with SessionLocal() as db:
            owner = db.get(Run, old).user_id
            file = FileObject(
                user_id=owner,
                filename=f"old-only-{action}.txt",
                size=3,
                status="ready",
                storage_key="audit/raw",
                meta={"text_key": "audit/text"},
            )
            db.add(file)
            db.flush()
            db.add(MessageFileRef(run_id=old, message_kind="run_prompt", message_id=old, file_id=file.id, role="agent_read", ordinal=0))
            summary = ContextSummary(
                facts=[SummaryEntry(id="f", text="old fact", sources=[SummarySource(file_id=file.id)])]
            ).model_dump_json()
            frame = ContextSummaryFrame(
                conversation_id=conv["id"],
                branch_anchor=old,
                revision=7,
                through_run_id=old,
                summary=summary,
                status="active",
                source_epoch=0,
                input_digest="test",
                revalidation_state={"pending": [{"source": {"file_id": file.id}, "offset": 0}]},
            )
            db.add(frame)
            db.flush()
            conversation = db.get(Conversation, conv["id"])
            conversation.summary, conversation.summary_through_run_id, conversation.summary_frame_id = summary, old, frame.id
            db.commit()
        get_storage().put_bytes("audit/text", b"OLD_BRANCH_ONLY")
        if action == "edit":
            run = c.post(f"/v1/runs/{old}/edit", headers=AUTH, json={"prompt": "new branch"}).json()
            with SessionLocal() as db:
                assert db.get(Conversation, conv["id"]).summary_frame_id is None
        else:
            c.post(f"/v1/conversations/{conv['id']}/active", headers=AUTH, json={"run_id": replacement["id"]})
            run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "next"}).json()
        url = f"/internal/runs/{run['id']}/model-context"
        ctx = c.get(url, headers=SERVICE).json()
        assert ctx["summary"] == "" and ctx["summary_evidence"] == []
        saved = c.put(
            f"/internal/runs/{run['id']}/model-summary",
            headers=attempt_headers(c, run["id"]),
            json={
                "summary": ContextSummary(objective="new summary").model_dump_json(),
                "through_run_id": root,
                "expected_through_run_id": None,
                "source_epoch": ctx["source_epoch"],
                "revalidation_ids": [],
            },
        )
        assert saved.status_code == 200, saved.text
        assert c.get(url, headers=SERVICE).json()["summary_evidence"] == []
        with SessionLocal() as db:
            frame = db.get(ContextSummaryFrame, db.get(Conversation, conv["id"]).summary_frame_id)
            assert frame.previous_id is None and frame.revalidation_state == {"pending": []}


def test_file_and_block_evidence_require_current_ancestor_references():
    from ailab_platform.api.summary_evidence import evidence
    from ailab_platform.models import DocumentBlock, FileDerivation

    with TestClient(app) as c:
        _conv, turns = create(c)
        root = turns[0]["id"]
        with SessionLocal() as db:
            owner = db.get(Run, root).user_id
            file = FileObject(
                user_id=owner,
                filename="other.txt",
                size=3,
                status="ready",
                storage_key="audit/other",
                meta={"text_key": "audit/other-text"},
            )
            db.add(file)
            db.flush()
            derivation = FileDerivation(
                file_id=file.id, kind="native", fingerprint="test", status="ready", parser_version="test", manifest_key="audit/manifest"
            )
            db.add(derivation)
            db.flush()
            block = DocumentBlock(
                derivation_id=derivation.id, unit_number=1, text_key="audit/other-text", anchor="p:1", digest="test", chars=10, ordinal=0
            )
            db.add(block)
            db.flush()
            get_storage().put_bytes("audit/other-text", b"OTHER_PATH")
            sources = [SummarySource(file_id=file.id), SummarySource(block_id=block.id)]
            frame = ContextSummaryFrame(
                revision=7,
                status="active",
                summary=ContextSummary(facts=[SummaryEntry(id="f", text="x", sources=sources)]).model_dump_json(),
            )
            assert evidence(db, frame, owner, {root}) == []
            db.add(MessageFileRef(run_id=root, message_kind="run_prompt", message_id=root, file_id=file.id, role="agent_read", ordinal=0))
            db.flush()
            assert len(evidence(db, frame, owner, {root})) == 2
            query = select(MessageFileRef.file_id).where(MessageFileRef.run_id.in_([root]))
            postgres = db.bind.dialect.name == "postgresql"
            if postgres:
                db.execute(text("SET LOCAL enable_seqscan=off"))
            sql = str(query.compile(db.bind, compile_kwargs={"literal_binds": True}))
            plan = db.execute(text(("EXPLAIN " if postgres else "EXPLAIN QUERY PLAN ") + sql)).all()
            assert "INDEX" in str(plan).upper(), plan


def test_valid_summary_frame_keeps_its_unconsumed_file_cursor():
    with TestClient(app) as c:
        conv, turns = create(c, ("chat", "chat"))
        root, leaf = [r["id"] for r in turns]
        with SessionLocal() as db:
            owner = db.get(Run, root).user_id
            file = FileObject(
                user_id=owner,
                filename="cursor.txt",
                size=9000,
                status="ready",
                storage_key="audit/cursor-raw",
                meta={"text_key": "audit/cursor-text"},
            )
            db.add(file)
            db.flush()
            db.add(MessageFileRef(run_id=root, message_kind="run_prompt", message_id=root, file_id=file.id, role="agent_read", ordinal=0))
            semantic = ContextSummary(facts=[SummaryEntry(id="f", text="fact", sources=[SummarySource(file_id=file.id)])]).model_dump_json()
            frame = ContextSummaryFrame(
                conversation_id=conv["id"],
                branch_anchor=root,
                through_run_id=root,
                summary=semantic,
                revision=7,
                status="active",
                source_epoch=0,
                input_digest="cursor",
                revalidation_state={"pending": [{"source": {"file_id": file.id}, "offset": 0}]},
            )
            db.add(frame)
            db.flush()
            previous_id = frame.id
            conversation = db.get(Conversation, conv["id"])
            conversation.summary, conversation.summary_through_run_id, conversation.summary_frame_id = semantic, root, frame.id
            db.commit()
        get_storage().put_bytes("audit/cursor-text", b"x" * 9000)
        run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "next"}).json()
        context = c.get(f"/internal/runs/{run['id']}/model-context", headers=SERVICE).json()
        assert context["summary_evidence"][0]["through_char"] == 4000
        saved = c.put(
            f"/internal/runs/{run['id']}/model-summary",
            headers=attempt_headers(c, run["id"]),
            json={
                "summary": semantic,
                "through_run_id": leaf,
                "expected_through_run_id": root,
                "source_epoch": context["source_epoch"],
                "revalidation_ids": [context["summary_evidence"][0]["evidence_id"]],
            },
        )
        assert saved.status_code == 200, saved.text
        with SessionLocal() as db:
            current = db.get(ContextSummaryFrame, db.get(Conversation, conv["id"]).summary_frame_id)
            assert current.previous_id == previous_id
            assert current.revalidation_state["pending"][0]["offset"] == 4000
