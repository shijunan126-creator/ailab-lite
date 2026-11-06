"""Browser-first document views, one durable generation and one published result.

Browser generations own no server render slot. Only after their claim expires
or reports failure can the Worker request the existing bounded render pipeline.
"""

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select

from ..contracts.document_view import PARSER_REVISION
from ..events import append_event
from ..models import ClientCall, DocumentOperation, FileObject, RenderedPage, Run, ViewerCapture
from . import document_operations as operations
from .jobs import stage_job

TERMINAL = {"ready", "failed", "canceled"}


def requested(db, run, file, body):
    if body.cursor:
        raise HTTPException(409, "cursor_stale")
    target = body.target.model_dump(mode="json", exclude_none=True)
    from . import view_state

    views = sorted(
        (v for v in view_state.views(run.user_id, run.conversation_id) if v.get("file_id") == file.id and v.get("version") == file.sha256),
        key=lambda v: v["view_id"],
    )
    representation = views[0].get("representation_revision") if views else None
    operation = operations.request(
        db,
        run,
        lane="render",
        kind="chat_view",
        file_id=file.id,
        version=file.sha256,
        binding={"target": target, "renderer": PARSER_REVISION, "representation_revision": representation},
        priority=1,
    )
    operations.attach_consumer(db, run, operation)
    if not operation.job_id:
        operation.job_id = operation.id
        operation.status = "browser"
        operation.generation = 1
        operation.binding = {
            **operation.binding,
            "browser_deadline": min(operations.aware(operation.deadline), datetime.now(UTC) + timedelta(seconds=20)).isoformat(),
        }
        job = stage_job(db, "document_view", file_id=file.id, job_id=operation.id, priority=True)
        job.payload = {**job.payload, "deadline": operations.aware(operation.deadline).isoformat()}
    db.flush()
    return response(operation, file)


def response(row, file):
    base = {"file_id": file.id, "version": file.sha256, "operation_id": row.id}
    if row.status == "ready":
        files = row.result["files"]
        source = files[0].get("source") if files else None
        meta = file.meta or {}
        count = (meta.get("ocr_preview") or {}).get("page_count") or meta.get("render_page_count")
        return {
            **base,
            "files": files,
            "coverage": row.binding["target"],
            "missing": [],
            "truncated": False,
            "visual_representation": {
                "source": source,
                "page_count": count,
                "region_revision": "use the selected files entry image_revision, never a native index revision",
                "sheet_to_page_mapping": "unavailable" if file.filename.lower().endswith(".xlsx") else "not_applicable",
            },
        }
    if row.status in {"failed", "canceled"}:
        return {**base, "error": row.result.get("reason", "document_view_failed"), "partial": bool(row.result.get("files"))}
    return {**base, "pending": True, "action": "document-view", "deadline": operations.aware(row.deadline).timestamp()}


def fail(db, row, reason):
    revoke_browser(db, row)
    from ..library import schedule_object_deletion
    from .derived_assets import unpin

    for index in range(4):
        unpin(db, f"document-view-input:{row.id}:{index}")
    for item in row.result.get("files", []):
        capture = db.get(ViewerCapture, item.get("capture_id")) if item.get("capture_id") else None
        if capture:
            unpin(db, "capture:" + capture.id)
            schedule_object_deletion(db, capture.storage_key, "capture")
            db.delete(capture)
    row.status, row.claim_token, row.lease_until = "failed", None, None
    row.result = {**row.result, "reason": reason}
    wake(db, row)


def wake(db, row):
    from .file_dependencies import wake_file

    file = db.get(FileObject, row.file_id)
    if file:
        wake_file(db, file)


def revoke_browser(db, row):
    call_id = (row.result or {}).get("call_id")
    call = db.get(ClientCall, (row.run_id, call_id)) if call_id else None
    if call and call.status in {"pending", "claimed"}:
        call.status, call.claim_token = "canceled", None
        call.result = {"ok": False, "error": {"type": "unavailable", "message": "capture generation superseded"}}


def fallback(db, row):
    # User row lock serializes this transition with claim/result publication on
    # both databases. Revocation and generation replacement commit together.
    revoke_browser(db, row)
    from ..library import schedule_object_deletion
    from .derived_assets import unpin

    for item in row.result.get("files", []):
        capture = db.get(ViewerCapture, item.get("capture_id"))
        if capture:
            unpin(db, "capture:" + capture.id)
            schedule_object_deletion(db, capture.storage_key, "capture")
            db.delete(capture)
    row.generation += 1
    row.status = "preparing"
    row.result = {"files": []}


def browser_step(db, row, file):
    target = row.binding["target"]
    kind = file.filename.rsplit(".", 1)[-1].lower()
    if target["kind"] != "pages" or kind not in {"pdf", "pptx", "docx"}:
        fallback(db, row)
        return
    from . import view_state
    from .routes_client_calls import expire

    run = db.get(Run, row.run_id)
    now = datetime.now(UTC)
    if (run.options or {}).get("client") != "portal" or now >= datetime.fromisoformat(row.binding["browser_deadline"]):
        fallback(db, row)
        return
    files = (row.result or {}).get("files", [])
    if len(files) == len(target["pages"]):
        row.status = "ready"
        wake(db, row)
        return
    call_id = (row.result or {}).get("call_id")
    call = db.get(ClientCall, (row.run_id, call_id)) if call_id else None
    if call:
        expire(db, call, run)
        if call.status in {"pending", "claimed"}:
            return
        if call.status != "completed" or not (call.result or {}).get("ok"):
            fallback(db, row)
            return
        call_id = None
    states = [
        v
        for v in view_state.views(run.user_id, run.conversation_id)
        if v.get("file_id") == file.id
        and v.get("version") == file.sha256
        and v.get("representation_revision") == row.binding.get("representation_revision")
    ]
    if not states:
        fallback(db, row)
        return
    # Pick one live view deterministically; its tab must win the durable claim.
    view = sorted(states, key=lambda v: v["view_id"])[0]
    page = target["pages"][len(files)]
    at = ("slide:" if kind == "pptx" else "page:") + str(page)
    call_id = "dv:" + hashlib.sha256(f"{row.id}:{row.generation}:{page}".encode()).hexdigest()
    deadline = min(datetime.fromisoformat(row.binding["browser_deadline"]), now + timedelta(seconds=12))
    binding = {
        **view,
        "at": at,
        "source": "browser",
        "representation": PARSER_REVISION,
        "operation_id": row.id,
        "generation": row.generation,
        "page": page,
    }
    db.add(
        ClientCall(
            run_id=row.run_id,
            call_id=call_id,
            kind="capture_view",
            status="pending",
            created_at=now,
            claim_deadline=min(deadline, now + timedelta(seconds=3)),
            deadline=deadline,
            result={"_capture": binding, "_document": row.id},
        )
    )
    row.result = {**row.result, "call_id": call_id}
    append_event(
        db,
        row.run_id,
        "client.call",
        {
            "call_id": call_id,
            "kind": "capture_view",
            "code": "",
            "input": {"view_id": view["view_id"], "at": at, "max_width": 1600},
            "timeout_ms": max(100, min(10000, int((deadline - now).total_seconds() * 1000))),
            "deadline": deadline.isoformat(),
        },
        commit=False,
    )


def validate_browser(db, run, call, binding, value):
    row = db.get(DocumentOperation, binding["operation_id"], populate_existing=True)
    if (
        not row
        or row.run_id != run.id
        or row.status != "browser"
        or row.generation != binding["generation"]
        or row.result.get("call_id") != call.call_id
        or not operations.source_available(db, row)
        or datetime.now(UTC) >= datetime.fromisoformat(row.binding["browser_deadline"])
        or value.get("at") != binding["at"]
    ):
        raise HTTPException(409, "document_view_generation_changed")
    return row


def bind_browser(db, run, call, binding, receipt):
    row = db.get(DocumentOperation, binding["operation_id"])
    capture = db.get(ViewerCapture, receipt["capture_id"])
    db.flush()
    row.result = {
        **row.result,
        "files": [
            *row.result.get("files", []),
            {
                "capture_id": receipt["capture_id"],
                "file_id": row.file_id,
                "filename": "文档页图",
                "page": binding["page"],
                "content_type": "image/png",
                "source": "browser",
                "version": row.source_version,
                "representation_revision": binding.get("representation_revision") or PARSER_REVISION,
                "image_revision": capture.digest,
                "location_verified": True,
                "pixels_verified": False,
            },
        ],
    }
    if len(row.result["files"]) == len(row.binding["target"]["pages"]):
        row.status = "ready"
        wake(db, row)


def server_inputs(db, row, file):
    from .document_ocr import visual_input
    from .routes_ocr import request_page_render

    target = row.binding["target"]
    if target["kind"] == "pages":
        selectors = [{"kind": "pages", "pages": [page]} for page in target["pages"]]
    elif target["kind"] == "image":
        if target["image_id"] != file.id:
            capture = db.get(ViewerCapture, target["image_id"])
            if not capture or capture.file_id != file.id or capture.digest != target["revision"]:
                raise HTTPException(409, "document_image_revision_changed")
            from .viewer_captures import image

            image(db, row.run_id, capture.id)
            return [
                {
                    "key": capture.storage_key,
                    "digest": capture.digest,
                    "revision": capture.digest,
                    "page": 0,
                    "source": capture.target["source"],
                    "region": None,
                }
            ]
        if not file.content_type.startswith("image/"):
            raise HTTPException(422, "embedded_image_unavailable_use_pages")
        selectors = [
            {"kind": "region", "image_id": file.id, "revision": target["revision"], "rotation": 0, "x": 0, "y": 0, "width": 1, "height": 1}
        ]
    else:
        selectors = [target]
    pages = target.get("pages", [target["page"]] if target.get("page") else [])
    if pages and not file.content_type.startswith("image/"):
        known = ((file.meta or {}).get("ocr_preview") or {}).get("page_count") or (file.meta or {}).get("render_page_count")
        if type(known) is int and any(page > known for page in pages):
            raise HTTPException(422, "document_page_out_of_range")
        present = set(
            db.scalars(
                select(RenderedPage.page).where(
                    RenderedPage.file_id == file.id, RenderedPage.version == "poppler-1280-v1", RenderedPage.page.in_(pages)
                )
            )
        )
        missing = [page for page in pages if page not in present]
        if missing:
            previous = row.result.get("render_job_id")
            if (
                previous
                and (file.meta or {}).get("preview_job_id") == previous
                and ((file.meta or {}).get("preview") or {}).get("status") == "failed"
            ):
                raise HTTPException(422, "document_page_images_failed")
            request_page_render(db, file, missing, run=operations.live_consumer(db, row))
            row.result = {**row.result, "render_job_id": file.meta.get("preview_job_id")}
            return None
    images = []
    for selector in selectors:
        item = visual_input(db, row, file, selector)
        if item is None:
            return None
        images.append(item)
    return images
