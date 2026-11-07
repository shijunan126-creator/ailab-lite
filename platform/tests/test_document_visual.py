"""Browser execution stays exclusive until a durable generation hands off."""

# ruff: noqa: F811

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from ailab_platform.api import document_visual as visual
from ailab_platform.api import routes_document_visual as routes
from ailab_platform.contracts.document_input import ViewDocument
from ailab_platform.models import ClientCall, DocumentLane, DocumentOperation, FileObject, OutboxMessage, RenderedPage, Run
from tests.test_capture_assets import setup_capture
from tests.test_derived_assets import ledger  # noqa: F401


def prepare(factory, storage, monkeypatch, *, pages=(1,), target=None, filename=None):
    setup_capture(factory, storage, monkeypatch)
    with factory.begin() as db:
        db.add_all([DocumentLane(name="render"), DocumentLane(name="ocr")])
        db.flush()
        run = db.get(Run, "r")
        run.options = {**run.options, "client": "portal"}
        if filename:
            db.get(FileObject, "f").filename = filename
        body = ViewDocument(file_id="f", target=target or {"kind": "pages", "pages": list(pages)})
        result = visual.requested(db, run, db.get(FileObject, "f"), body)
        assert visual.requested(db, run, db.get(FileObject, "f"), body) == result
        row = db.get(DocumentOperation, result["operation_id"])
        db.flush()
        job = db.get(OutboxMessage, row.id)
        job.claim_token, job.claimed_until = "worker", datetime.now(UTC) + timedelta(minutes=5)
        return row.id


def test_browser_pending_has_no_render_or_ocr_job_and_takeover_revokes_old_result(ledger, monkeypatch):
    factory, storage = ledger
    operation_id = prepare(factory, storage, monkeypatch)
    monkeypatch.setattr(
        "ailab_platform.api.view_state.views", lambda user, conversation: [{"view_id": "view", "file_id": "f", "version": "source"}]
    )
    with factory() as db:
        row, file = db.get(DocumentOperation, operation_id), db.get(FileObject, "f")
        visual.browser_step(db, row, file)
        db.commit()
        call = db.get(ClientCall, ("r", row.result["call_id"]))
        binding = call.result["_capture"]
        assert binding["at"] == "page:1" and row.status == "browser"
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == 1
        assert db.scalar(select(DocumentOperation.id).where(DocumentOperation.status == "running")) is None
        call.status, call.claim_token = "claimed", "tab-one"
        db.commit()
        visual.validate_browser(db, db.get(Run, "r"), call, binding, {"at": "page:1"})
        visual.fallback(db, row)
        db.commit()
        assert row.status == "preparing" and row.generation == 2
        assert call.status == "canceled" and call.claim_token is None
        with pytest.raises(HTTPException) as error:
            visual.validate_browser(db, db.get(Run, "r"), call, binding, {"at": "page:1"})
        assert error.value.status_code == 409


def test_browser_success_stays_in_one_canonical_asset_and_never_requests_fallback(ledger, monkeypatch):
    from ailab_platform.api.viewer_captures import save
    from ailab_platform.models import ViewerCapture

    factory, storage = ledger
    operation_id = prepare(factory, storage, monkeypatch)
    monkeypatch.setattr(
        "ailab_platform.api.view_state.views", lambda user, conversation: [{"view_id": "view", "file_id": "f", "version": "source"}]
    )
    with factory() as db:
        row, file, run = db.get(DocumentOperation, operation_id), db.get(FileObject, "f"), db.get(Run, "r")
        visual.browser_step(db, row, file)
        db.commit()
        call = db.get(ClientCall, ("r", row.result["call_id"]))
        binding = call.result["_capture"]
        result = save(db, run, call.call_id, binding, b"pixels", bind=lambda receipt: visual.bind_browser(db, run, call, binding, receipt))
        assert row.status == "ready" and row.generation == 1
        assert row.result["files"][0]["capture_id"] == result["capture_id"]
        assert db.scalar(select(func.count()).select_from(ViewerCapture)) == 1
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == 1
        assert "browser" == row.result["files"][0]["source"]
        assert not row.result["files"][0]["pixels_verified"]
        # A stored capture is addressed by its own digest, never the page render digest.
        ref = visual.response(row, file)["files"][0]["region_ref"]
        assert ref == {"image_id": result["capture_id"], "revision": row.result["files"][0]["image_revision"]}


def test_offline_cached_pages_reuse_original_canonical_objects_without_ocr_or_encoding(ledger, monkeypatch):
    factory, storage = ledger
    operation_id = prepare(factory, storage, monkeypatch, pages=(1, 2))
    monkeypatch.setattr("ailab_platform.api.view_state.views", lambda user, conversation: [])
    with factory() as db:
        db.add_all(
            [
                RenderedPage(file_id="f", page=page, version="poppler-1280-v1", key=f"derived/f/page-{page}.png", digest=f"digest-{page}")
                for page in (1, 2)
            ]
        )
        db.commit()
        result = routes.step(operation_id, "worker", db)
        assert result["status"] == "ready", result
        files = result["result"]["files"]
        assert [f["page"] for f in files] == [1, 2]
        assert all(f["source"] == "native-pdf" and not f.get("capture_id") for f in files)
        row, file = db.get(DocumentOperation, operation_id), db.get(FileObject, "f")
        refs = [item["region_ref"] for item in visual.response(row, file)["files"]]
        assert refs == [{"page": 1, "revision": "digest-1"}, {"page": 2, "revision": "digest-2"}]
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == 1


def test_view_operation_cancel_releases_all_partial_browser_assets(ledger, monkeypatch):
    from ailab_platform.api import derived_assets as assets
    from ailab_platform.api.viewer_captures import save
    from ailab_platform.models import DerivedAssetRef, ViewerCapture

    factory, storage = ledger
    operation_id = prepare(factory, storage, monkeypatch, pages=(1, 2))
    monkeypatch.setattr(
        "ailab_platform.api.view_state.views", lambda user, conversation: [{"view_id": "view", "file_id": "f", "version": "source"}]
    )
    with factory() as db:
        row, file, run = db.get(DocumentOperation, operation_id), db.get(FileObject, "f"), db.get(Run, "r")
        visual.browser_step(db, row, file)
        db.commit()
        call = db.get(ClientCall, ("r", row.result["call_id"]))
        binding = call.result["_capture"]
        save(db, run, call.call_id, binding, b"partial", bind=lambda receipt: visual.bind_browser(db, run, call, binding, receipt))
        visual.fail(db, row, "canceled")
        db.commit()
        assert db.scalar(select(ViewerCapture.id)) is None
        assert db.scalar(select(DerivedAssetRef.id)) is None
        assets.collect_batch(db, storage, transient_only=True)
        assert not storage.exists(f"derived/f/captures/{row.result['files'][0]['capture_id']}")


def test_uncached_offline_view_stages_one_render_and_keeps_its_source_binding(ledger, monkeypatch):
    factory, storage = ledger
    operation_id = prepare(factory, storage, monkeypatch)
    monkeypatch.setattr("ailab_platform.api.view_state.views", lambda user, conversation: [])
    with factory() as db:
        file = db.get(FileObject, "f")
        file.filename = "source.xlsx"
        db.commit()
        first = routes.step(operation_id, "worker", db)
        assert first["status"] == "preparing", first
        row = db.get(DocumentOperation, operation_id)
        job_id = row.result["render_job_id"]
        assert job_id and db.get(FileObject, "f").meta["preview_job_id"] == job_id
        second = routes.step(operation_id, "worker", db)
        assert second["status"] == "preparing", second
        assert db.get(DocumentOperation, operation_id).result["render_job_id"] == job_id
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == 2


def test_interrupted_view_consumer_keeps_only_its_exact_render_dependency_alive(ledger, monkeypatch):
    from ailab_platform.api import file_work

    factory, storage = ledger
    operation_id = prepare(factory, storage, monkeypatch)
    monkeypatch.setattr("ailab_platform.api.view_state.views", lambda user, conversation: [])
    with factory() as db:
        result = routes.step(operation_id, "worker", db)
        assert result["status"] == "preparing", result
        parent = db.get(DocumentOperation, operation_id)
        child = db.get(DocumentOperation, parent.result["render_job_id"])
        run = db.get(Run, "r")
        run.status = "interrupted"
        run.runtime_meta = {**run.runtime_meta, "file_dependency": {"operation_id": parent.id}}
        db.commit()
        assert file_work.available(db, child)
        parent.result = {**parent.result, "render_job_id": "unrelated-job"}
        db.commit()
        assert not file_work.available(db, child)


def finish_outline(db, starts, page_count):
    """What a completed first-page render of a workbook leaves behind."""
    file = db.get(FileObject, "f")
    meta = dict(file.meta)
    meta["preview"] = {"status": "ready", "kind": "pages"}
    if starts:
        meta["render_sheet_pages"] = {"version": file.sha256, "page_count": page_count, "sheets": starts}
    file.meta = meta
    db.add_all(
        [
            RenderedPage(file_id="f", page=page, version="poppler-1280-v1", key=f"derived/f/page-{page}.png", digest=f"digest-{page}")
            for page in range(1, page_count + 1)
        ]
    )
    db.commit()


def test_sheet_view_learns_the_outline_once_then_shows_that_sheets_pages(ledger, monkeypatch):
    factory, storage = ledger
    target = {"kind": "sheet", "sheet": "scanned"}
    operation_id = prepare(factory, storage, monkeypatch, target=target, filename="book.xlsx")
    monkeypatch.setattr("ailab_platform.api.view_state.views", lambda user, conversation: [])
    with factory() as db:
        first = routes.step(operation_id, "worker", db)
        assert first["status"] == "preparing" and first["waiting_for"] == "page-images"
        row, file = db.get(DocumentOperation, operation_id), db.get(FileObject, "f")
        assert row.result["render_job_id"] == file.meta["preview_job_id"] and file.meta["ocr_render_pages"] == [1]
        # The outline render is this view's own render dependency, so a waiting
        # consumer keeps it alive instead of the job being cancelled as orphaned.
        from ailab_platform.api import file_work

        run = db.get(Run, "r")
        run.status = "interrupted"
        run.runtime_meta = {**run.runtime_meta, "file_dependency": {"operation_id": row.id}}
        db.commit()
        assert file_work.available(db, db.get(DocumentOperation, row.result["render_job_id"]))
        run.status = "running"
        db.commit()
        jobs = db.scalar(select(func.count()).select_from(OutboxMessage))
        assert routes.step(operation_id, "worker", db)["status"] == "preparing"
        assert db.scalar(select(func.count()).select_from(OutboxMessage)) == jobs
        finish_outline(db, [{"sheet": "Revenue", "first_page": 1}, {"sheet": "Scanned", "first_page": 2}], 7)
        result = routes.step(operation_id, "worker", db)
        assert result["status"] == "ready", result
        assert [item["page"] for item in result["result"]["files"]] == [2, 3, 4, 5]
        view = visual.response(db.get(DocumentOperation, operation_id), db.get(FileObject, "f"))
        assert view["coverage"] == {**target, "pages": [2, 3, 4, 5]} and view["truncated"]
        assert view["visual_representation"]["sheet_to_page_mapping"] == [
            {"sheet": "Revenue", "pages": [1, 1]},
            {"sheet": "Scanned", "pages": [2, 7]},
        ]


@pytest.mark.parametrize(
    ("sheet", "starts", "error"),
    [
        ("Hidden", [{"sheet": "Revenue", "first_page": 1}], "sheet_not_rendered"),
        ("Revenue", None, "sheet_page_mapping_unavailable"),
    ],
)
def test_sheet_view_reports_unprinted_sheets_and_missing_outlines(ledger, monkeypatch, sheet, starts, error):
    factory, storage = ledger
    operation_id = prepare(factory, storage, monkeypatch, target={"kind": "sheet", "sheet": sheet}, filename="book.xlsx")
    monkeypatch.setattr("ailab_platform.api.view_state.views", lambda user, conversation: [])
    with factory() as db:
        assert routes.step(operation_id, "worker", db)["status"] == "preparing"
        finish_outline(db, starts, 2)
        assert routes.step(operation_id, "worker", db)["status"] == "failed"
        assert visual.response(db.get(DocumentOperation, operation_id), db.get(FileObject, "f"))["error"] == error


def test_sheet_view_takes_no_cell_range():
    with pytest.raises(ValueError, match="range"):
        ViewDocument(file_id="f", target={"kind": "sheet", "sheet": "Revenue", "range": "A1:B2"})
