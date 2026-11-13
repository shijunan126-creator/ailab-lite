"""Run-level admission must cover steers as well as the initial prompt."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from ailab_platform.api import routes_inbox
from ailab_platform.api.document_preparation import bind
from ailab_platform.api.native_ranges import charge_consumption
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.models import Conversation, QueuedMessage, Run, new_id
from tests.test_native_document_scopes import make_file


@pytest.mark.parametrize("case", ["bytes", "structure", "allowed"])
def test_steer_claim_charges_cumulative_files_before_input_assembly(monkeypatch, case):
    init_db()
    with SessionLocal() as db:
        files = [make_file(db)[0] for _ in range(3)]
        for file in files:
            file.size = (20 if case == "bytes" else 1) * 1024**2
            if case == "structure":
                file.meta = {**file.meta, "extract": {"source_chars": 80000}}
        conv = Conversation(user_id=files[0].user_id, title="steer")
        db.add(conv)
        db.flush()
        run = Run(
            user_id=conv.user_id,
            conversation_id=conv.id,
            prompt="read",
            model="m",
            status="running",
            options={"mode": "chat"},
            runtime_meta={},
        )
        db.add(run)
        db.flush()
        conv.active_run_id = run.id
        for file in files[:2]:
            charge_consumption(db, run, file)
        row = QueuedMessage(
            conversation_id=conv.id,
            user_id=conv.user_id,
            run_id=run.id,
            prompt="more",
            model="m",
            state="steered",
            steer=True,
            file_ids=[files[2].id],
            options={"document_input_plan": {"preparation_id": new_id("prep"), "items": []}},
        )
        db.add(row)
        db.commit()
        monkeypatch.setattr(routes_inbox, "fence_attempt", lambda *args: None)
        if case == "allowed":
            monkeypatch.setattr("ailab_platform.api.attachment_input.input_files", lambda *a, **kw: [])
            assert len(routes_inbox.claim_steers(run.id, db=db)["items"]) == 1
            assert len(run.runtime_meta["document_inputs"]) == 3
            # Redelivery before Agent persists steer_ids must not charge twice.
            routes_inbox.claim_steers(run.id, db=db)
            assert len(run.runtime_meta["document_inputs"]) == 3
        else:
            monkeypatch.setattr(
                "ailab_platform.api.attachment_input.input_files", lambda *a, **kw: pytest.fail("over-budget input assembled")
            )
            with pytest.raises(HTTPException) as caught:
                routes_inbox.claim_steers(run.id, db=db)
            assert (caught.value.status_code, caught.value.detail) == (
                413,
                "document_input_limit" if case == "bytes" else "document_structure_limit",
            )
            db.rollback()
            assert len(db.get(Run, run.id).runtime_meta["document_inputs"]) == 2


@pytest.mark.parametrize("existing", [False, True])
def test_steer_budget_merges_earliest_deadline_and_preserves_ocr_on_reclaim(existing):
    init_db()
    with SessionLocal() as db:
        file, _ = make_file(db)
        now = datetime.now(UTC)
        saved = {"started_at": now.isoformat(), "deadline": (now + timedelta(seconds=600)).isoformat()}
        old = {
            "started_at": (now - timedelta(seconds=10)).isoformat(),
            "deadline": (now + timedelta(seconds=590)).isoformat(),
            "ocr_batches": 2,
            "ocr_pages": {file.id: [1, 2]},
        }
        run = Run(
            id=new_id("run"), user_id=file.user_id, prompt="read", model="m", runtime_meta={"document_budget": old} if existing else {}
        )
        plan = {"preparation_id": new_id("prep"), "budget": saved}
        bind(db, run, plan)
        expected = dict(run.runtime_meta["document_budget"])
        bind(db, run, plan)
        assert run.runtime_meta["document_budget"] == expected
        assert expected["deadline"] == (old if existing else saved)["deadline"]
        if existing:
            assert expected["ocr_batches"] == 2 and expected["ocr_pages"] == old["ocr_pages"]
        earlier = {
            **plan,
            "budget": {"started_at": (now - timedelta(seconds=30)).isoformat(), "deadline": (now + timedelta(seconds=570)).isoformat()},
        }
        bind(db, run, earlier)
        assert run.runtime_meta["document_budget"]["deadline"] == earlier["budget"]["deadline"]
