"""Forks preserve program-owned file identities, deletion fences and summary cursors."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import (
    AppliedMessagePosition,
    ContextSummaryFrame,
    ContextSummarySource,
    Conversation,
    MessageFileRef,
    QueuedMessage,
    Run,
)
from tests.test_conversations import _finish
from tests.test_document_context import AUTH, SERVICE, upload


def seed(c):
    fid = upload(c, "original.txt", b"original file evidence", "text/plain")
    conv = c.post("/v1/conversations", headers=AUTH, json={}).json()
    run = c.post(f"/v1/conversations/{conv['id']}/messages", headers=AUTH, json={"prompt": "read", "mode": "work"}).json()
    assert c.post(f"/internal/runs/{run['id']}/library/read", headers=SERVICE, json={"file_id": fid}).status_code == 200
    with SessionLocal() as db:
        owner = db.get(Run, run["id"]).user_id
        q = QueuedMessage(
            conversation_id=conv["id"],
            user_id=owner,
            prompt="steered file",
            file_ids=[fid],
            model=run["model"],
            options={"mode": "work"},
            steer=True,
            state="steered",
            run_id=run["id"],
            after_run_id=run["id"],
        )
        db.add(q)
        db.flush()
        qid = q.id
        db.get(Run, run["id"]).runtime_meta = {"steer_ids": [qid]}
        db.add(
            MessageFileRef(
                run_id=run["id"],
                message_kind="queued_message",
                message_id=qid,
                file_id=fid,
                ordinal=1,
                role="input",
                reference_origin="upload",
                filename_snapshot="original.txt",
                upload_id="upload-original",
            )
        )
        db.add(AppliedMessagePosition(run_id=run["id"], message_kind="queued_message", message_id=qid, application_seq=1))
        db.commit()
    _finish(c, run["id"], "file evidence used")
    return conv, run, fid


def fork(c, conv, run):
    response = c.post(f"/v1/conversations/{conv['id']}/fork", headers=AUTH, json={"run_id": run["id"], "mode": "work"})
    assert response.status_code == 201, response.text
    branch = response.json()
    copied = c.get(f"/v1/conversations/{branch['id']}", headers=AUTH).json()["turns"][0]
    return branch, copied


def test_fork_preserves_steer_positions_and_file_provenance():
    with TestClient(app) as c:
        conv, run, fid = seed(c)
        branch, copied = fork(c, conv, run)
        assert copied["result"]["forked_from_run_id"] == run["id"]
        response = c.post(f"/internal/runs/{copied['id']}/library/resolve", headers=SERVICE, json={"relative": "previous"}).json()
        assert response["status"] == "resolved" and response["candidates"][0]["file_id"] == fid
        with SessionLocal() as db:
            refs = list(db.scalars(select(MessageFileRef).where(MessageFileRef.run_id == copied["id"])))
            assert {r.role for r in refs} == {"agent_read", "input"}
            ref = next(r for r in refs if r.role == "input")
            assert ref.upload_id == "upload-original" and ref.reference_origin == "upload"
        follow = c.post(f"/v1/conversations/{branch['id']}/messages", headers=AUTH, json={"prompt": "continue", "mode": "work"}).json()
        history = c.get(f"/internal/runs/{follow['id']}/model-context", headers=SERVICE).json()["history"]
        assert history[0]["attachments"][0]["message_kind"] == "queued_message"


@pytest.mark.parametrize("delete_before_fork", [False, True])
def test_fork_retains_deletion_fence_for_tool_reads_and_steers(delete_before_fork):
    with TestClient(app) as c:
        conv, run, fid = seed(c)
        if delete_before_fork:
            c.delete(f"/v1/files/{fid}", headers=AUTH)
        branch, _copied = fork(c, conv, run)
        if not delete_before_fork:
            c.delete(f"/v1/files/{fid}", headers=AUTH)
        follow = c.post(f"/v1/conversations/{branch['id']}/messages", headers=AUTH, json={"prompt": "continue", "mode": "work"}).json()
        history = c.get(f"/internal/runs/{follow['id']}/model-context", headers=SERVICE).json()["history"]
        assert history[0]["reply"] == "（文件来源已失效，需重新读取）"


def test_fork_remaps_summary_sources_and_pending_revalidation():
    with TestClient(app) as c:
        conv, run, fid = seed(c)
        semantic = {
            "schema_version": 1,
            "facts": [{"id": "fact", "text": "file evidence", "sources": [{"run_id": run["id"], "file_id": fid}]}],
        }
        with SessionLocal() as db:
            frame = ContextSummaryFrame(
                conversation_id=conv["id"],
                branch_anchor=run["id"],
                revision=7,
                through_run_id=run["id"],
                source_epoch=0,
                input_digest="old",
                summary=json.dumps(semantic),
                revalidation_state={"pending": [{"source": {"run_id": run["id"], "file_id": fid}, "offset": 3}]},
            )
            db.add(frame)
            db.flush()
            db.add(ContextSummarySource(frame_id=frame.id, file_id=fid))
            old = db.get(Conversation, conv["id"])
            old.summary, old.summary_through_run_id, old.summary_frame_id = frame.summary, run["id"], frame.id
            db.commit()
            old_frame_id = frame.id
        branch, copied = fork(c, conv, run)
        with SessionLocal() as db:
            new = db.get(Conversation, branch["id"])
            assert new.summary_frame_id and new.summary_frame_id != old_frame_id
            frame = db.get(ContextSummaryFrame, new.summary_frame_id)
            assert frame.revalidation_state["pending"][0] == {"source": {"run_id": copied["id"], "file_id": fid}, "offset": 3}
            assert json.loads(new.summary)["facts"][0]["sources"][0]["run_id"] == copied["id"]
            assert db.get(ContextSummarySource, (frame.id, fid))
        follow = c.post(f"/v1/conversations/{branch['id']}/messages", headers=AUTH, json={"prompt": "continue", "mode": "work"}).json()
        context = c.get(f"/internal/runs/{follow['id']}/model-context", headers=SERVICE).json()
        assert context["summary_evidence"][0]["run_id"] == copied["id"]
        assert context["summary_evidence"][0]["from_char"] == 3
        _finish(c, follow["id"], "new branch content")
        next_run = c.post(
            f"/v1/conversations/{branch['id']}/messages", headers=AUTH, json={"prompt": "compact again", "mode": "work"}
        ).json()
        from tests.run_claims import attempt_headers

        with SessionLocal() as db:
            summary = db.get(Conversation, branch["id"]).summary
        response = c.put(
            f"/internal/runs/{next_run['id']}/model-summary",
            headers=attempt_headers(c, next_run["id"]),
            json={
                "expected_through_run_id": copied["id"],
                "through_run_id": follow["id"],
                "summary": summary,
                "source_epoch": context["source_epoch"],
                "revalidation_ids": [item["evidence_id"] for item in context["summary_evidence"]],
            },
        )
        assert response.status_code == 200, response.text


def test_skill_forces_work_attachment_admission_and_input_strategy():
    with TestClient(app) as c:
        fid = upload(c, "partial.txt", ("字" * 200010).encode(), "text/plain")
        response = c.post("/v1/runs", headers=AUTH, json={"prompt": "read ranges", "mode": "chat", "skill": "pptx", "file_ids": [fid]})
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["options"]["mode"] == "work"
        prepared = c.get(f"/internal/runs/{run['id']}/model-input", headers=SERVICE).json()
        assert prepared["input_files"][0]["input_strategy"] == "reference"
        assert prepared["input_files"][0]["text"] == ""
