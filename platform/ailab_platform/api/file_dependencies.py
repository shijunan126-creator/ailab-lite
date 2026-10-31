"""Bounded reconciliation of suspended file tools; jobs and continuation commit together."""

import logging
import time
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select

from ..db import SessionLocal
from ..locks import user_critical_section
from ..models import FileObject, Run, RunStatus

_after = ""
log = logging.getLogger("ailab.api.file_dependencies")


def ready(db, dependency):
    if dependency.get("retry_at", 0) > time.time():
        return False
    file = db.get(FileObject, dependency["file_id"], populate_existing=True)
    if not file or file.deleted_at or file.purged_at:
        return True
    meta = file.meta or {}
    if time.time() >= dependency["deadline"]:
        if dependency["action"] == "extract-text" and meta.get("ocr") in {"pending", "running"}:
            file.meta = {**meta, "ocr": "failed", "ocr_reason": "deadline"}
        return True
    if dependency["action"] == "extract-text":
        from .routes_ocr import extraction_result

        result = extraction_result(db, file, dependency.get("pages"))
        return result["ocr"] not in {"pending", "running"}
    from ..models import RenderedPage
    from .document_content import visual_pages

    selected = dependency.get("pages")
    if selected is None:
        selected = visual_pages(file)
        if selected is None:
            preview = meta.get("ocr_preview") or {}
            if preview.get("status") == "ready" and preview.get("page_count"):
                selected = list(range(1, min(preview["page_count"], 20) + 1))
    if selected == []:
        return True
    if selected:
        cached = set(
            db.scalars(
                select(RenderedPage.page).where(
                    RenderedPage.file_id == file.id, RenderedPage.page.in_(selected[:20]), RenderedPage.version == "poppler-1280-v1"
                )
            )
        )
        if all(p in cached for p in selected[:20]):
            return True
    return (meta.get("ocr_preview") or {}).get("status") == "failed"


def reconcile():
    global _after
    with SessionLocal() as db:
        # ix_runs_status_id bounds each tick even with many manual pauses. Round
        # robin avoids starving dependencies behind older interrupted runs.
        rows = db.execute(
            select(Run.id, Run.user_id).where(Run.status == RunStatus.INTERRUPTED, Run.id > _after).order_by(Run.id).limit(100)
        ).all()
        _after = rows[-1].id if rows else ""
        for run_id, user_id in rows:
            with user_critical_section(db, user_id):
                run = db.get(Run, run_id, populate_existing=True)
                dependency = (run.runtime_meta or {}).get("file_dependency")
                if run.status != RunStatus.INTERRUPTED or not dependency:
                    db.rollback()
                    continue
                if not ready(db, dependency):
                    prepare(db, dependency)
                    db.commit()
                    continue
                resume_dependency(db, run)
                db.commit()


def prepare(db, dependency):
    """Ask for the rendered pages a waiting image reference needs. The tool asks once, but while another preview of
    the file is still pending (the sheet preview every new spreadsheet gets) this tick promotes its queued job.
    Once that job finishes, the next tick requests any pages still missing. Asking repeatedly does not create
    duplicate durable jobs."""
    if dependency["action"] != "image-reference" or dependency.get("retry_at", 0) > time.time():
        return
    file = db.get(FileObject, dependency["file_id"])
    if not file or file.deleted_at or file.purged_at or file.content_type.startswith("image/"):
        return
    from .routes_library_tools import document_images
    from .routes_ocr import request_page_render

    selected = dependency.get("pages")
    missing = document_images(db, file, selected, prepare=False)
    if missing.get("pending"):
        request_page_render(db, file, None if selected is None else missing["pages"])


def wake_file(db, file):
    """Fast completion path; the reconciler covers crashes, deadlines and overflow."""
    from ..models import MessageFileRef

    db.flush()
    candidates = db.scalars(
        select(Run)
        .where(
            Run.id.in_(select(MessageFileRef.run_id).where(MessageFileRef.file_id == file.id)),
            Run.user_id == file.user_id,
            Run.status == RunStatus.INTERRUPTED,
        )
        .limit(100)
    ).all()
    for run in candidates:
        dependency = (run.runtime_meta or {}).get("file_dependency")
        if dependency and dependency["file_id"] == file.id and ready(db, dependency):
            resume_dependency(db, run)


def resume_dependency(db, run):
    from ..run_state import transition_run
    from .routes_runtime import _admit_continuation, queue_continuation

    started = time.monotonic()
    try:
        _admit_continuation(db, run)
    except HTTPException as exc:
        if exc.status_code == 429:
            run.runtime_meta = {
                **run.runtime_meta,
                "file_dependency": {**run.runtime_meta["file_dependency"], "retry_at": time.time() + 10},
            }
            run.error = "附件已就绪，等待额度或并发资源恢复"
        else:
            run.runtime_meta = {k: v for k, v in run.runtime_meta.items() if k != "file_dependency"}
            transition_run(db, run.id, RunStatus.FAILED, error="附件任务无法继续：" + str(exc.detail), commit=False)
        return
    run.runtime_meta = {k: v for k, v in run.runtime_meta.items() if k != "file_dependency"}
    queue_continuation(db, run)
    waiting_since = run.waiting_since
    if waiting_since and waiting_since.tzinfo is None:
        waiting_since = waiting_since.replace(tzinfo=UTC)
    wait_seconds = max(0.0, (datetime.now(UTC) - waiting_since).total_seconds()) if waiting_since else 0.0
    log.info("file dependency phase=resume seconds=%.3f wait_seconds=%.3f", time.monotonic() - started, wait_seconds)
