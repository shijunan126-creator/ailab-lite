"""Integrated document coverage, identity, admission and discovery contracts."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from ailab_platform.agent import discovery
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.internal_skills import load
from ailab_platform.models import DocumentBlock, FileDerivation, MessageFileRef, Run
from ailab_platform.worker.pipeline import process_file
from tests.test_file_previews import AUTH, SERVICE, ApiClient
from tests.test_ocr import text_pdf


def upload(c, name, data, mime):
    response = c.post("/v1/files", headers=AUTH, files={"upload": (name, data, mime)})
    assert response.status_code == 201
    fid = response.json()["id"]
    process_file(fid, platform=ApiClient(c))
    return fid


def test_pdf_tail_and_persistent_blocks_across_modes():
    with TestClient(app) as c:
        fid = upload(c, "native.pdf", text_pdf(120), "application/pdf")
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "读取85页", "file_ids": [fid]}).json()
        assert c.get(f"/internal/runs/{run['id']}/model-input", headers=SERVICE).json()["input_files"][0]["text"] == ""
        url = f"/internal/runs/{run['id']}/library/read"
        result = c.post(url, headers=SERVICE, json={"file_id": fid, "pages": [85]}).json()
        assert "native text page 85" in result["text"]
        assert result["coverage"][0]["anchor"] == "page:85" and result["status"] == "ready"
        assert c.post(url, headers=SERVICE, json={"file_id": fid, "derivation_id": "wrong"}).status_code == 409
        with SessionLocal() as db:
            assert len(db.scalars(select(DocumentBlock).join(FileDerivation).where(FileDerivation.file_id == fid)).all()) == 120
            query = select(DocumentBlock.id).where(DocumentBlock.derivation_id == result["derivation_id"], DocumentBlock.unit_number == 85)
            sql = str(query.compile(db.bind, compile_kwargs={"literal_binds": True}))
            assert db.execute(text(("EXPLAIN QUERY PLAN " if db.bind.dialect.name == "sqlite" else "EXPLAIN ") + sql)).all()
        chat = c.post("/v1/runs", headers=AUTH, json={"prompt": "全文", "mode": "chat", "file_ids": [fid]})
        assert chat.status_code == 201
        prepared = c.get(f"/internal/runs/{chat.json()['id']}/model-input", headers=SERVICE).json()
        assert "native text page 120" in prepared["input_files"][0]["text"]


def test_reference_order_and_ambiguity_are_structural():
    with TestClient(app) as c:
        a = upload(c, "same.txt", b"first", "text/plain")
        b = upload(c, "same.txt", b"second", "text/plain")
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "compare", "file_ids": [b, a]}).json()
        url = f"/internal/runs/{run['id']}/library/resolve"
        result = c.post(url, headers=SERVICE, json={"relative": "current", "ordinal": 2}).json()
        assert result["status"] == "resolved" and result["candidates"][0]["file_id"] == a
        assert c.post(url, headers=SERVICE, json={"relative": "current"}).json()["status"] == "ambiguous"
        with SessionLocal() as db:
            refs = db.scalars(select(MessageFileRef).where(MessageFileRef.run_id == run["id"]).order_by(MessageFileRef.ordinal)).all()
            assert [r.file_id for r in refs] == [b, a]


def test_chat_rejects_partial_without_losing_original():
    with TestClient(app) as c:
        fid = upload(c, "large.txt", ("字" * 200010).encode(), "text/plain")
        response = c.post("/v1/runs", headers=AUTH, json={"prompt": "全文", "mode": "chat", "file_ids": [fid]})
        assert response.status_code == 422 and response.json()["detail"]["code"] == "chat_attachment_too_large"
        assert c.get(f"/v1/files/{fid}/content", headers=AUTH).status_code == 200
        assert c.post("/v1/runs", headers=AUTH, json={"prompt": "读", "mode": "work", "file_ids": [fid]}).status_code == 201


def test_delete_fences_active_context():
    with TestClient(app) as c:
        fid = upload(c, "source.txt", b"secret-source", "text/plain")
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "read", "file_ids": [fid]}).json()
        assert c.delete(f"/v1/files/{fid}", headers=AUTH).status_code == 204
        with SessionLocal() as db:
            row = db.get(Run, run["id"])
            assert row.status == "canceled" and row.runtime_meta["source_invalidated"]
        assert c.post(f"/internal/runs/{run['id']}/library/read", headers=SERVICE, json={"file_id": fid}).status_code == 404


def test_internal_skills_are_discoverable():
    from ailab_platform.agent.prompts import skills

    hit = discovery.search("pdf", 3, list(skills().values()))[0]
    assert hit["name"] == "platform.pdf"
    assert "next_cursor" in load("platform.pdf.read")
    for name in ("platform.document.read", "platform.spreadsheet.read", "platform.presentation.read", "platform.pdf.read"):
        assert "office-read" not in load(name, mode="chat")
        assert "office-read" in load(name, mode="work")
    with pytest.raises(ValueError):
        load("platform.pdf.read", "wrong")
    with TestClient(app) as c:
        assert any(s["name"] == "platform.pdf" for s in c.get("/v1/skills", headers=AUTH).json())
        assert c.post("/v1/runs", headers=AUTH, json={"prompt": "x", "skill": "platform.pdf.read"}).status_code == 201


def test_skill_search_does_not_change_tool_availability():
    import asyncio

    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.llm import LLMResponse, ToolCall
    from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": "work", "capability_discovery": 1}}

    platform, broker = Platform(), FakeBroker()
    model = ScriptedLLM(
        [
            LLMResponse(tool_calls=[ToolCall("s", "search_capabilities", {"query": "write_file", "kind": "tool"})]),
            LLMResponse(tool_calls=[ToolCall("w", "write_file", {"path": "/workspace/a.txt", "content": "kept"})]),
            LLMResponse(content="完成"),
        ]
    )
    asyncio.run(AgentLoop(platform, broker, model).process("run_1", attempt_id="a"))
    assert platform.statuses[-1][0] == "succeeded"
    assert not platform.checkpoint["state"].get("active_tools")
    assert broker.leases == 1


def test_file_backed_reads_register_deletion_dependency():
    with TestClient(app) as c:
        fid = upload(c, "not-in-prompt.txt", b"original", "text/plain")
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "search"}).json()
        response = c.post(f"/internal/runs/{run['id']}/library/read", headers=SERVICE, json={"file_id": fid})
        assert response.status_code == 200
        with SessionLocal() as db:
            assert db.scalar(
                select(MessageFileRef).where(
                    MessageFileRef.run_id == run["id"], MessageFileRef.file_id == fid, MessageFileRef.role == "agent_read"
                )
            )
        c.delete(f"/v1/files/{fid}", headers=AUTH)
        with SessionLocal() as db:
            assert db.get(Run, run["id"]).status == "canceled"


def test_same_bytes_private_cache_copies_are_independent():
    from ailab_platform.api.document_cache import reuse
    from ailab_platform.models import FileObject
    from ailab_platform.storage import get_storage

    with TestClient(app) as c:
        first = upload(c, "cache.pdf", text_pdf(2), "application/pdf")
        response = c.post("/v1/files", headers=AUTH, files={"upload": ("cache-copy.pdf", text_pdf(2), "application/pdf")})
        second = response.json()["id"]
        with SessionLocal() as db:
            source = reuse(db, db.get(FileObject, second))
            from ailab_platform.worker.office import copy_cache

            cached = copy_cache(second, source, get_storage())
            assert cached["cache"] == "private_copy"
            assert cached["text_key"] != db.get(FileObject, first).meta["text_key"]
            original = get_storage().get_bytes(cached["text_key"])
        c.delete(f"/v1/files/{first}", headers=AUTH)
        assert get_storage().get_bytes(cached["text_key"]) == original


def test_document_indexes_are_bounded_at_100000_blocks():
    import uuid

    from sqlalchemy import insert

    with TestClient(app) as c:
        fid = upload(c, "index.txt", b"index", "text/plain")
        with SessionLocal() as db:
            derivation = FileDerivation(
                file_id=fid,
                kind="benchmark",
                fingerprint=uuid.uuid4().hex,
                parser_version="test-index",
                status="ready",
                manifest_key="not-read",
            )
            db.add(derivation)
            db.flush()
            for start in range(0, 100000, 1000):
                db.execute(
                    insert(DocumentBlock),
                    [
                        dict(
                            id="blk_" + uuid.uuid4().hex[:20],
                            derivation_id=derivation.id,
                            ordinal=i,
                            unit_number=i + 1,
                            anchor=f"page:{i + 1}",
                            text_key="not-read",
                            digest="0" * 64,
                            chars=0,
                        )
                        for i in range(start, start + 1000)
                    ],
                )
            db.commit()
            query = (
                select(DocumentBlock.id)
                .where(DocumentBlock.derivation_id == derivation.id, DocumentBlock.unit_number == 85000)
                .order_by(DocumentBlock.ordinal)
                .limit(1)
            )
            sql = str(query.compile(db.bind, compile_kwargs={"literal_binds": True}))
            plan = " ".join(
                str(row) for row in db.execute(text(("EXPLAIN QUERY PLAN " if db.bind.dialect.name == "sqlite" else "EXPLAIN ") + sql))
            )
            assert "ix_document_blocks_unit" in plan
            assert db.scalar(query)
            import json
            import os
            from pathlib import Path

            evidence = os.environ.get("AILAB_DOCUMENT_EVIDENCE_DIR")
            if evidence:
                path = Path(evidence)
                path.mkdir(parents=True, exist_ok=True)
                (path / ("query-plan-" + db.bind.dialect.name + ".json")).write_text(json.dumps({"blocks": 100000, "plan": plan}, indent=2))


def test_deletion_does_not_poison_new_turns_after_the_source_fence():
    from ailab_platform.api.source_lifecycle import drain_invalidations, invalid_history

    with TestClient(app) as c:
        fid = upload(c, "generation.txt", b"source", "text/plain")
        conversation = c.post("/v1/conversations", headers=AUTH, json={}).json()
        old = c.post(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH, json={"prompt": "read", "file_ids": [fid]}).json()
        c.delete(f"/v1/files/{fid}", headers=AUTH)
        new = c.post(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH, json={"prompt": "fresh request"})
        assert new.status_code == 201
        with SessionLocal() as db:
            assert old["id"] in invalid_history(db, [old["id"], new.json()["id"]])
            assert new.json()["id"] not in invalid_history(db, [old["id"], new.json()["id"]])
            for _ in range(3):
                drain_invalidations(db)
            assert db.get(Run, new.json()["id"]).status == "queued"


def test_file_deletion_fences_dream_raw_recall_and_snapshot_replay(monkeypatch):
    from datetime import UTC, datetime

    from ailab_platform.api.source_lifecycle import drain_invalidations
    from ailab_platform.memory_dream.service import _new_runs
    from ailab_platform.memory_recall.policy import run_permissions
    from ailab_platform.models import FileSourceInvalidation, MemoryDreamRun, User

    with TestClient(app) as c:
        fid = upload(c, "memory-source.txt", b"private evidence", "text/plain")
        old = c.post("/v1/runs", headers=AUTH, json={"prompt": "source", "file_ids": [fid]}).json()
        with SessionLocal() as db:
            run = db.get(Run, old["id"])
            run.status = "succeeded"
            run.summary = "private evidence from file"
            run.result = {"thinking": {"text": "private evidence"}, "artifacts": []}
            from ailab_platform.events import append_event
            from ailab_platform.models import RunDelivery

            append_event(db, run.id, "agent.tool", {"result": "private evidence"}, commit=False)
            db.add(RunDelivery(run_id=run.id, delivery_id="confirmed-call", result={"text": "private evidence"}))
            owner = db.get(User, run.user_id)
            owner.settings = {
                **(owner.settings or {}),
                "memory": {
                    "entries": [
                        {"id": "auto", "content": "derived", "source": "auto", "evidence_run_ids": [run.id]},
                        {
                            "id": "explicit",
                            "content": "user saved",
                            "source": "user",
                            "source_quote": "private evidence",
                            "evidence_run_ids": [run.id],
                        },
                    ]
                },
            }
            dream = MemoryDreamRun(
                user_id=owner.id,
                local_date="2026-10-29",
                status="applied",
                before_memory=owner.settings["memory"],
                after_memory=owner.settings["memory"],
                plan={"changes": [{"content": "derived", "evidence_run_ids": [run.id]}]},
            )
            db.add(dream)
            db.commit()
            dream_id, uid = dream.id, owner.id
        assert c.delete(f"/v1/files/{fid}", headers=AUTH).status_code == 204
        assert c.get(f"/v1/runs/{old['id']}/events/history", headers=AUTH).json() == []
        detail = c.get(f"/v1/runs/{old['id']}", headers=AUTH).json()
        assert "private evidence" not in str(detail)
        with SessionLocal() as db:
            run = db.get(Run, old["id"])
            from ailab_platform.api.routes_memory import memory_for

            online = memory_for(db.get(User, uid))["entries"]
            assert len(online) == 1 and online[0]["source_quote"] == ""
            assert run_permissions(db, run) == {"read": False, "write": False}
            from fastapi import HTTPException

            from ailab_platform.api import runtime_fence

            monkeypatch.setattr(runtime_fence, "fence_attempt", lambda *args: None)
            with pytest.raises(HTTPException) as late:
                with runtime_fence.fenced_run(db, run, "late-attempt"):
                    pytest.fail("deleted-source writer admitted")
            assert late.value.detail == "run_source_unavailable"
            assert run.id not in {r.id for r in _new_runs(db, uid, None, datetime.now(UTC))}
            for _ in range(1000):
                drain_invalidations(db, limit=100)
                if db.get(FileSourceInvalidation, fid).state == "done":
                    break
            assert db.get(FileSourceInvalidation, fid).state == "done"
            snapshot = db.get(MemoryDreamRun, dream_id)
            assert [e["id"] for e in snapshot.before_memory["entries"]] == ["explicit"]
            assert snapshot.before_memory["entries"][0]["source_quote"] == ""
            assert snapshot.plan["changes"] == []
            from ailab_platform.models import RunEvent

            assert all(e.payload == {"source_unavailable": True} for e in db.scalars(select(RunEvent).where(RunEvent.run_id == run.id)))
            assert db.get(RunDelivery, (run.id, "confirmed-call")).result == {"source_unavailable": True}
            assert run.summary == "（文件来源已失效）"


def test_ocr_cache_reads_early_pages_beyond_recent_metadata():
    from ailab_platform.api.document_sources import ocr_records
    from ailab_platform.models import FileObject, OcrPageResult

    with TestClient(app) as c:
        fid = upload(c, "ocr-cache.txt", b"cache", "text/plain")
        with SessionLocal() as db:
            for page in range(50):
                db.add(
                    OcrPageResult(
                        file_id=fid,
                        page=page,
                        fingerprint="test",
                        generation="g",
                        text_key=f"page/{page}",
                        engine="rapidocr",
                        backend="CANN",
                        seconds=0.1,
                    )
                )
            db.commit()
            records = ocr_records(db, db.get(FileObject, fid), [0, 49])
            assert set(records) == {"0", "49"}


def test_summary_revalidation_is_periodic_bounded_and_source_fenced():
    from ailab_platform.api.summary_evidence import evidence
    from ailab_platform.contracts.context_summary import ContextSummary, SummaryEntry, SummarySource
    from ailab_platform.models import ContextSummaryFrame

    with TestClient(app) as c:
        fid = upload(c, "calibration.pdf", text_pdf(2), "application/pdf")
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "original constraint", "file_ids": [fid]}).json()
        with SessionLocal() as db:
            source = db.get(Run, run["id"])
            block = db.scalar(select(DocumentBlock).join(FileDerivation).where(FileDerivation.file_id == fid))
            summary = ContextSummary(
                constraints=[
                    SummaryEntry(id="c", text="old claim", sources=[SummarySource(run_id=source.id), SummarySource(block_id=block.id)])
                ]
            )
            frame = ContextSummaryFrame(status="active", revision=6, summary=summary.model_dump_json())
            assert evidence(db, frame, source.user_id, {source.id}) == []
            frame.revision = 7
            results = evidence(db, frame, source.user_id, {source.id})
            assert len(results) == 2
            assert "original constraint" in results[0]["text"]
            assert all(r["scope"] == "bounded_excerpt" for r in results)
            assert sum(len(r["text"]) for r in results) <= 32000
        c.delete(f"/v1/files/{fid}", headers=AUTH)
        with SessionLocal() as db:
            assert evidence(db, frame, source.user_id, {source.id}) == []


def test_revalidation_progress_survives_frames_and_rejects_false_acknowledgement():
    from ailab_platform.api.summary_evidence import advance, evidence
    from ailab_platform.contracts.context_summary import ContextSummary, SummaryEntry, SummarySource
    from ailab_platform.models import ContextSummaryFrame

    with TestClient(app) as c:
        run = c.post("/v1/runs", headers=AUTH, json={"prompt": "连续原文" * 1800}).json()
        with SessionLocal() as db:
            source = db.get(Run, run["id"])
            semantic = ContextSummary(constraints=[SummaryEntry(id="c", text="需校准", sources=[SummarySource(run_id=source.id)])])
            frame = ContextSummaryFrame(revision=7, status="active", summary=semantic.model_dump_json(), revalidation_state={})
            offered = evidence(db, frame, source.user_id, {source.id})
            assert offered[0]["from_char"] == 0 and not offered[0]["source_complete"]
            no_ack = advance(db, frame, source.user_id, {source.id}, [])
            assert no_ack["pending"][0]["offset"] == 0
            with pytest.raises(ValueError):
                advance(db, frame, source.user_id, {source.id}, ["invented"])
            progress = advance(db, frame, source.user_id, {source.id}, [offered[0]["evidence_id"]])
            next_frame = ContextSummaryFrame(revision=8, status="active", summary=semantic.model_dump_json(), revalidation_state=progress)
            second = evidence(db, next_frame, source.user_id, {source.id})
            assert second[0]["from_char"] == offered[0]["through_char"]
            assert offered[0]["text"] + second[0]["text"] == source.prompt + "\n"
            assert advance(db, next_frame, source.user_id, {source.id}, [second[0]["evidence_id"]]) == {"pending": []}


def test_restore_tombstones_preserve_deletion_and_are_idempotent():
    from sqlalchemy import delete

    from ailab_platform.models import FileObject, FileSourceInvalidation, User

    with TestClient(app) as c:
        fid = upload(c, "restore-source.txt", b"private restored source", "text/plain")
        c.delete(f"/v1/files/{fid}", headers=AUTH)
        exported = c.get("/internal/file-source-tombstones", headers=SERVICE).json()
        item = next(item for item in exported["items"] if item["file_id"] == fid)
        with SessionLocal() as db:
            file = db.get(FileObject, fid)
            file.deleted_at = None  # fixture: restored a pre-deletion database backup
            db.execute(delete(FileSourceInvalidation).where(FileSourceInvalidation.file_id == fid))
            db.commit()
        body = {"schema_version": 1, "items": [{**item, "purged": True}]}
        result = c.post("/internal/file-source-tombstones", headers=SERVICE, json=body)
        assert result.status_code == 200 and result.json()["applied"] == 1
        assert c.get(f"/v1/files/{fid}/content", headers=AUTH).status_code == 410
        with SessionLocal() as db:
            assert db.get(FileObject, fid).purged_at
            epoch = db.get(User, item["user_id"]).context_source_epoch
        assert c.post("/internal/file-source-tombstones", headers=SERVICE, json=body).json()["applied"] == 0
        with SessionLocal() as db:
            assert db.get(User, item["user_id"]).context_source_epoch == epoch
        assert c.get("/internal/file-source-tombstones", headers=AUTH).status_code == 403


def test_ocr_cache_paginates_privately_and_rejects_deleted_source(monkeypatch):
    from ailab_platform.config import settings
    from ailab_platform.models import FileObject, OcrPageResult

    monkeypatch.setattr(settings, "ocr_engine_fingerprint", "cache-test")
    with TestClient(app) as c:
        source = upload(c, "cache-pages.txt", b"identical", "text/plain")
        target = c.post("/v1/files", headers=AUTH, files={"upload": ("copy.txt", b"identical", "text/plain")}).json()["id"]
        with SessionLocal() as db:
            db.add_all(
                [
                    OcrPageResult(
                        file_id=source,
                        page=page,
                        fingerprint="cache-test",
                        generation="source-generation",
                        text_key=f"derived/{source}/ocr/{page}.txt",
                        engine="rapidocr:test",
                        backend="cpu",
                        seconds=1,
                    )
                    for page in range(105)
                ]
            )
            db.commit()
        url = f"/internal/files/{target}/document-cache-pages"
        first = c.get(url, headers=SERVICE, params={"source_id": source}).json()
        assert len(first["pages"]) == 100 and first["next_after"] == 99
        second = c.get(url, headers=SERVICE, params={"source_id": source, "after": first["next_after"]}).json()
        assert len(second["pages"]) == 5 and second["next_after"] is None
        generation = "a" * 32
        records = [
            {
                "page": page["page"],
                "source_generation": page["generation"],
                "generation": generation,
                "text_key": f"derived/{target}/ocr-cache/{generation}/{page['page']}.txt",
            }
            for page in second["pages"]
        ]
        assert c.post(url, headers=SERVICE, json={"source_id": source, "pages": records}).json()["copied_pages"] == 5
        with SessionLocal() as db:
            assert db.get(OcrPageResult, (target, 104, "cache-test")).seconds == 0
            assert db.get(FileObject, target).meta["ocr_copied_pages"] == 5
        assert c.delete(f"/v1/files/{source}", headers=AUTH).status_code == 204
        assert c.get(url, headers=SERVICE, params={"source_id": source}).status_code == 409


def test_summary_revalidation_reads_completed_ocr_when_native_text_is_absent():
    from ailab_platform.api.summary_evidence import evidence
    from ailab_platform.contracts.context_summary import ContextSummary, SummaryEntry, SummarySource
    from ailab_platform.models import ContextSummaryFrame, FileObject
    from ailab_platform.storage import get_storage

    with TestClient(app) as c:
        fid = upload(c, "ocr-source.txt", b"fixture", "text/plain")
        with SessionLocal() as db:
            file = db.get(FileObject, fid)
            key = f"derived/{fid}/ocr.txt"
            get_storage().put_bytes(key, "识别编号 AL-219".encode(), "text/plain")
            file.meta = {"ocr": "done", "ocr_text_key": key}
            db.commit()
            summary = ContextSummary(facts=[SummaryEntry(id="f", text="编号", sources=[SummarySource(file_id=fid)])])
            frame = ContextSummaryFrame(revision=7, status="active", summary=summary.model_dump_json(), revalidation_state={})
            assert evidence(db, frame, file.user_id, set())[0]["text"] == "识别编号 AL-219"


def test_a_tool_the_mode_allows_runs_when_called_by_name_before_discovery():
    """Available tools can be called without a preceding skill search."""
    import asyncio

    from ailab_platform.agent.loop import AgentLoop
    from ailab_platform.llm import LLMResponse, ToolCall
    from tests.test_agent_loop import FakeBroker, FakePlatform, ScriptedLLM

    class Platform(FakePlatform):
        async def get_run(self, run_id):
            return {**await super().get_run(run_id), "options": {"mode": "work", "capability_discovery": 1}}

    platform, broker = Platform(), FakeBroker()
    model = ScriptedLLM(
        [
            LLMResponse(tool_calls=[ToolCall("w", "write_file", {"path": "/workspace/a.txt", "content": "kept"})]),
            LLMResponse(content="完成"),
        ]
    )
    asyncio.run(AgentLoop(platform, broker, model).process("run_1", attempt_id="a"))
    result = next(payload for name, payload in platform.events if name == "agent.tool_result")
    assert result["ok"] is True, result
    assert not platform.checkpoint["state"].get("active_tools")
