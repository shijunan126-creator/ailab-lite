"""Whole-file packing and persisted preparation budgets, without implicit OCR/rendering."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from ailab_platform.api.document_input_plan import prepare
from ailab_platform.api.document_preparation import bind, get_preparation, start_budget
from ailab_platform.catalog import ModelSpec
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.domain.document_input_plan import choose_inline
from ailab_platform.models import DocumentPreparation, OutboxMessage, Run, User, new_id
from ailab_platform.storage import get_storage
from tests.test_native_document_scopes import make_file


def item(identity, ordinal, *, tokens=1000, images=0, eligible=True, document_images=None):
    return {
        "file_id": identity,
        "ordinal": ordinal,
        "eligible": eligible,
        "text_tokens": tokens,
        "images": images,
        "document_images": images if document_images is None else document_images,
    }


def test_inline_packing_moves_largest_whole_file_and_breaks_ties_by_ordinal():
    files = [item("large", 0, tokens=18000), item("a", 1, tokens=5000), item("b", 2, tokens=5000)]
    assert choose_inline(files, 200000, 1000) == {"a", "b"}
    assert choose_inline([item("a", 0, tokens=5000), item("b", 1, tokens=5000)], 30000, 1000) == {"b"}
    assert choose_inline([item("a", 0, tokens=6000), item("b", 1, tokens=6000)], 100000, 1000) == {"a", "b"}


def test_inline_page_images_are_all_or_on_demand_not_first_eight():
    assert choose_inline([item("ten", 0, images=10)], 200000, 1000) == set()
    assert choose_inline([item("five", 0, images=5), item("four", 1, images=4)], 200000, 1000) == {"four"}
    assert choose_inline([item("standalone", 0, images=5, document_images=0), item("eight", 1, images=8)], 200000, 1000) == {
        "standalone",
        "eight",
    }
    assert choose_inline([item("unknown", 0, eligible=False)], 200000, 1000) == set()


def test_preparation_http_retries_keep_first_start_and_run_inherits_deadline():
    init_db()
    with SessionLocal() as db:
        file, _ = make_file(db)
        user = db.get(User, file.user_id)
        send_id = str(uuid4())
        row = get_preparation(db, user, [file], "m", "first", send_id)
        deadline = start_budget(db, row)
        started = row.started_at
        repeated = get_preparation(db, user, [file], "m", "first", send_id)
        assert repeated.id == row.id and start_budget(db, repeated) == deadline
        assert repeated.started_at == started
        plan = {"preparation_id": row.id, "items": []}
        row.plan = plan
        run = Run(id=new_id("run"), user_id=user.id, prompt="first", model="m", runtime_meta={})
        db.add(run)
        db.flush()
        bind(db, run, plan)
        assert datetime.fromisoformat(run.runtime_meta["document_budget"]["deadline"]) == deadline
        row.deadline = datetime.now(UTC) - timedelta(seconds=1)
        with pytest.raises(HTTPException) as caught:
            start_budget(db, row)
        assert caught.value.status_code == 408
        assert row.started_at == started


def test_preparation_identity_binds_source_and_model_and_adopts_legacy_preflight():
    init_db()
    with SessionLocal() as db:
        file, _ = make_file(db)
        user = db.get(User, file.user_id)
        legacy = get_preparation(db, user, [file], "m", "prompt", None)
        deadline = start_budget(db, legacy)
        send_id = str(uuid4())
        adopted = get_preparation(db, user, [file], "m", "prompt", send_id)
        assert adopted.id == legacy.id and start_budget(db, adopted) == deadline
        with pytest.raises(HTTPException) as caught:
            get_preparation(db, user, [file], "changed-model", "prompt", send_id)
        assert caught.value.status_code == 409
        file.sha256 = "changed-source"
        with pytest.raises(HTTPException):
            get_preparation(db, user, [file], "m", "prompt", send_id)


def test_long_pdf_prepared_probe_becomes_on_demand_without_ocr_or_page_render():
    init_db()
    with SessionLocal() as db:
        file, index = make_file(db, total=500000)
        key = index.manifest_key
        get_storage().put_bytes(
            key,
            json.dumps(
                {
                    "parser": index.parser_version,
                    "version": 2,
                    "kind": "pdf",
                    "units": [],
                    "total_units": 500000,
                    "chars": 0,
                    "status": "partial",
                    "visual_status": "unknown",
                    "visual_units": [],
                }
            ).encode(),
        )
        file.meta = {**file.meta, "document_manifest_key": key}
        db.flush()
        plan = prepare(
            db,
            db.get(User, file.user_id),
            [file],
            ModelSpec("m", "m", capabilities=["tools", "vision"]),
            "find section",
            send_id=str(uuid4()),
        )
        assert not plan["items"][0]["inline"] and plan["items"][0]["units"] == 500000
        assert plan["items"][0]["visual"]["status"] == "unknown"
        assert db.scalar(select(OutboxMessage.id).where(OutboxMessage.payload["file_id"].as_string() == file.id).limit(1)) is None
        assert "document_budget" not in file.meta
        assert plan == prepare(
            db,
            db.get(User, file.user_id),
            [file],
            ModelSpec("m", "m", capabilities=["tools", "vision"]),
            "find section",
            send_id=db.get(DocumentPreparation, plan["preparation_id"]).send_id,
        )


def test_non_tool_model_can_inline_native_text_but_cannot_accept_partial_pdf():
    init_db()
    with SessionLocal() as db:
        file, index = make_file(db)
        key = index.manifest_key
        get_storage().put_bytes(
            key,
            json.dumps(
                {
                    "parser": index.parser_version,
                    "version": 2,
                    "kind": "pdf",
                    "units": [],
                    "total_units": 500,
                    "chars": 0,
                    "status": "partial",
                    "visual_status": "unknown",
                    "visual_units": [],
                }
            ).encode(),
        )
        file.meta = {**file.meta, "document_manifest_key": key}
        with pytest.raises(HTTPException) as caught:
            prepare(db, db.get(User, file.user_id), [file], ModelSpec("m", "m", capabilities=[]), "read", send_id=str(uuid4()))
        assert caught.value.detail["code"] == "chat_document_tools_required"
        body = b"Native text to inline"
        unit = {
            "ordinal": 0,
            "anchor": "page:1",
            "unit_number": 1,
            "name": "text.txt",
            "chars": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "byte_offset": 0,
            "byte_size": len(body),
            "quality": "native_unverified",
        }
        index.status = "ready"
        get_storage().put_bytes(key.rsplit("/", 1)[0] + "/text.txt", body)
        get_storage().put_bytes(
            key,
            json.dumps(
                {
                    "parser": index.parser_version,
                    "version": 2,
                    "kind": "pdf",
                    "units": [unit],
                    "total_units": 1,
                    "chars": len(body),
                    "status": "ready",
                    "visual_status": "none",
                    "visual_units": [],
                }
            ).encode(),
        )
        plan = prepare(db, db.get(User, file.user_id), [file], ModelSpec("m", "m", capabilities=[]), "read", send_id=str(uuid4()))
        assert plan["items"][0]["inline"]
        assert not plan["items"][0].get("visual_pages")
