"""Explicit representation preparation for tests whose subject is already-readable content.

Upload lifecycle tests must not use this helper: it deliberately consumes the
safe file after upload, using real API grants, jobs and the limited reader.
"""


def prepare_native(api, fid, *, full_pdf=False):
    from ailab_platform.api.derived_assets import backfill_batch
    from ailab_platform.api.native_preparation import request_native
    from ailab_platform.db import SessionLocal
    from ailab_platform.locks import user_critical_section
    from ailab_platform.models import DerivedBudget, FileObject
    from ailab_platform.storage import get_storage
    from ailab_platform.worker.native_preparation import process_native
    from tests.test_file_previews import ApiClient

    platform = ApiClient(api)
    with SessionLocal() as db:
        for _ in range(100):
            backfill_batch(db, get_storage())
            db.commit()
            if db.get(DerivedBudget, 1, populate_existing=True).backfill_complete:
                break
        assert db.get(DerivedBudget, 1).backfill_complete
        file = db.get(FileObject, fid)
        assert file.status == "ready"
        with user_critical_section(db, file.user_id):
            state = request_native(db, file, full_pdf=full_pdf)
            db.commit()
    if state["status"] == "pending":
        claim = platform._post(f"/internal/jobs/{state['job_id']}/claim", {"consumer": "explicit-fixture-reader"})
        platform.attempt = claim["token"]
        process_native(platform, fid, state["job_id"])
        platform._post(f"/internal/jobs/{state['job_id']}/ack", {"token": platform.attempt})
    with SessionLocal() as db:
        return db.get(FileObject, fid).meta["representations"]["native_text"]


def read_document(api, run_id, file_id, *, op="read_document", start=True, **body):
    """The model's document reader on a Chat run: the run is set running and called with its owning attempt."""
    from tests.run_claims import attempt_headers

    if start:
        from tests.test_conversations import _status

        _status(api, run_id, "running")
    return api.post(f"/internal/runs/{run_id}/documents/{op}", headers=attempt_headers(api, run_id), json={"file_id": file_id, **body})


def legacy_run(run, *, mode=None):
    """An existing run keeps the tool catalog it was originally given."""
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import Run

    with SessionLocal() as db:
        row = db.get(Run, run["id"])
        row.options = {**row.options, **({"mode": mode} if mode else {})}
        run["options"] = dict(row.options)
        db.commit()
    return run


def legacy_pending(fid):
    """Construct an already queued v1 record to exercise migration compatibility."""
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import FileObject

    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.status == "pending"
        file.meta = {**file.meta, "input_lifecycle": 1}
        db.commit()


def minimal_file(name, mime):
    import io
    from pathlib import Path

    output = io.BytesIO()
    kind = Path(name).suffix.lower()
    if kind == ".pdf" or mime == "application/pdf":
        from pypdf import PdfWriter

        pdf = PdfWriter()
        pdf.add_blank_page(width=612, height=792)
        pdf.write(output)
        pdf.close()
    elif kind == ".docx":
        from docx import Document

        doc = Document()
        doc.add_paragraph("preview fixture")
        doc.save(output)
    elif kind == ".pptx":
        from pptx import Presentation

        ppt = Presentation()
        ppt.slides.add_slide(ppt.slide_layouts[6])
        ppt.save(output)
    elif kind == ".xlsx":
        from openpyxl import Workbook

        book = Workbook()
        book.active["A1"] = "preview fixture"
        book.save(output)
        book.close()
    else:
        return b"hello"
    return output.getvalue()


def initialize_accounting():
    """Complete the bounded migration before an explicit test-owned writer."""
    from ailab_platform.api.derived_assets import backfill_batch
    from ailab_platform.db import SessionLocal
    from ailab_platform.models import DerivedBudget
    from ailab_platform.storage import get_storage

    with SessionLocal() as db:
        for _ in range(100):
            backfill_batch(db, get_storage())
            db.commit()
            if db.get(DerivedBudget, 1, populate_existing=True).backfill_complete:
                return
        raise AssertionError("test-owned storage migration did not finish")
