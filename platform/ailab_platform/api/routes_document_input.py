"""New-catalog Chat text operations, with current source and attempt admission.

Catalog V2 is selected only by API-owned run options; old runs cannot discover
or call these endpoints. Full rollout also requires the visual/OCR lifecycle.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..contracts.document_input import ReadDocument, SearchDocument
from ..deps import get_db
from ..domain.document_tools import catalog_version
from ..models import RunStatus
from ..service_auth import require_scope
from .document_text import available_ocr, current_index, text_operation
from .native_preparation import request_native
from .routes_internal import _run_or_404
from .routes_library_tools import authorized_source
from .runtime_fence import fenced_run

router = APIRouter(prefix="/internal/runs", dependencies=[Depends(require_scope("files:read_for_run"))])


def text_request(db, run_id, body, attempt):
    run = _run_or_404(db, run_id)
    if catalog_version(run.options) != 2 or (run.options or {}).get("mode", "work") != "chat":
        raise HTTPException(422, "document_tool_not_available_for_catalog")
    with fenced_run(db, run, attempt):
        if run.status != RunStatus.RUNNING:
            raise HTTPException(409, "run_unavailable")
        file = authorized_source(db, run, body.file_id)
        if (isinstance(body, ReadDocument) and body.text_source == "ocr") or (isinstance(body, SearchDocument) and available_ocr(db, file)):
            result = text_operation(db, file, body)
            db.commit()
            return result
        try:
            current_index(db, file)
        except HTTPException as exc:
            if exc.detail != "native_representation_unavailable":
                raise
            if body.cursor:
                raise HTTPException(409, "cursor_stale") from None
            from ..models import OutboxMessage
            from .document_operations import budget

            deadline = budget(run, start=True)
            state = request_native(db, file)
            job = db.get(OutboxMessage, state.get("job_id"))
            if job:
                from datetime import datetime

                existing_deadline = datetime.fromisoformat(job.payload["deadline"])
                job.payload = {**job.payload, "deadline": min(existing_deadline, deadline).isoformat()}
            db.commit()
            if state["status"] == "failed":
                raise HTTPException(422, "native_representation_failed")
            return {"file_id": file.id, "version": file.sha256, "pending": True, "action": "native-text", "deadline": deadline.timestamp()}
        result = text_operation(db, file, body)
        db.commit()
        return result


@router.post("/{run_id}/documents/read_document")
def read_document(run_id: str, body: ReadDocument, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    return text_request(db, run_id, body, x_run_attempt)


@router.post("/{run_id}/documents/search_document")
def search_document(run_id: str, body: SearchDocument, x_run_attempt: str | None = Header(default=None), db: Session = Depends(get_db)):
    return text_request(db, run_id, body, x_run_attempt)
