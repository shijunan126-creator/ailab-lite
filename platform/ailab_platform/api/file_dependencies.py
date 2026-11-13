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
    if not file or file.deleted_at or file.purged_at or time.time() >= dependency["deadline"]:
        return True
    if dependency["action"] in {"document-ocr", "native-scope", "document-view"}:
        from ..models import DocumentOperation

        operation = db.get(DocumentOperation, dependency.get("operation_id"), populate_existing=True)
        return not operation or operation.status in {"ready", "failed", "canceled"}
    if dependency["action"] == "native-text":
        from .native_preparation import native_state

        return native_state(file).get("status") in {"ready", "failed"}
    # A dependency of a retired tool: resume now, and the run meets the current catalog.
    return True


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
                    prepare(db, dependency, run=run)
                    db.commit()
                    continue
                resume_dependency(db, run)
                db.commit()


def prepare(db, dependency, *, run=None):
    """Keep native text moving for a waiting read. Document operations are driven by their durable outbox jobs."""
    if dependency["action"] != "native-text":
        return
    file = db.get(FileObject, dependency["file_id"])
    if file and not file.deleted_at and not file.purged_at:
        from .native_preparation import request_native

        request_native(db, file, run=run)


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
