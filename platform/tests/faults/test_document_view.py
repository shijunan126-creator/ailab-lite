"""Browser absence survives interruptions honestly: no claimer means expired, never a silent render."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ailab_platform.db import SessionLocal
from ailab_platform.models import ClientCall, RunEvent
from tests.test_document_view import capture_setup
from tests.test_ledger_protocol import AUTH, SERVICE, api  # noqa: F401


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
