"""Browser absence and durable reveal publication survive interruptions honestly."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ailab_platform.api.preview_dispatch import dispatch_preview_jobs
from ailab_platform.db import SessionLocal
from ailab_platform.models import ClientCall, RunEvent
from tests.test_document_view import capture_setup, document
from tests.test_ledger_protocol import AUTH, SERVICE, api  # noqa: F401
from tests.test_runtime_protocol import owned


def test_no_viewport_claimer_expires_without_render_fallback(api):  # noqa: F811
    rid, _, _, _, _, _ = capture_setup(api)
    with SessionLocal() as db:
        call = db.get(ClientCall, (rid, "capture"))
        call.status = "pending"
        call.claim_token = None
        call.claim_deadline = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    response = api.get(f"/internal/runs/{rid}/client-calls/capture", headers=SERVICE)
    assert response.json()["status"] == "expired"
    assert response.json()["result"]["error"]["type"] == "unavailable"
    assert api.post(f"/v1/runs/{rid}/client-calls/capture/claim", headers=AUTH).status_code == 409
    with SessionLocal() as db:
        assert not list(db.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == "document.reveal")))


def test_reveal_outbox_recovers_after_response_lost(api):  # noqa: F811
    fid, _, _ = document(api)
    rid, _, headers = owned(api)
    headers = {**headers, "X-Delivery-Id": rid + ":lost-response"}
    url = f"/internal/runs/{rid}/document-view"
    payload = {"op": "open", "file_id": fid}
    first = api.post(url, headers=headers, json=payload)
    assert first.status_code == 200
    # No dispatcher ran before the caller lost its response. Replay uses the durable command identity.
    second = api.post(url, headers=headers, json=payload)
    assert second.json() == first.json()
    dispatch_preview_jobs()
    dispatch_preview_jobs()
    with SessionLocal() as db:
        events = list(db.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == "document.reveal")))
        assert len(events) == 1


def test_revoked_source_cancels_reveal_before_publication(api):  # noqa: F811
    from uuid import uuid4

    from ailab_platform.models import FileObject, OutboxMessage

    fid, version, _ = document(api)
    rid, _, _ = owned(api)
    with SessionLocal() as db:
        db.add(
            OutboxMessage(
                id="view_" + uuid4().hex,
                topic="viewer.event",
                payload={
                    "run_id": rid,
                    "type": "document.reveal",
                    "payload": {
                        "command_id": "stale",
                        "file_id": fid,
                        "version": version,
                        "filename": "old.docx",
                        "targets": [{"at": "p:1", "quote": "old quote"}],
                    },
                },
            )
        )
        db.get(FileObject, fid).deleted_at = datetime.now(UTC)
        db.commit()
    dispatch_preview_jobs()
    with SessionLocal() as db:
        assert not list(db.scalars(select(RunEvent).where(RunEvent.run_id == rid, RunEvent.type == "document.reveal")))
