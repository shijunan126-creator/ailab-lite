import hashlib
import time

from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, RenderedPage
from ailab_platform.storage import get_storage
from tests.test_ledger_protocol import AUTH, api  # noqa: F401
from tests.test_service_identity import identities  # noqa: F401


def test_page_commit_is_job_bound_and_visible(api, identities):  # noqa: F811
    response = api.post("/v1/files", headers=AUTH, files={"upload": ("deck.pdf", b"pdf", "application/pdf")})
    fid = response.json()["id"]
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.status = "ready"
        file.meta = {
            **(file.meta or {}),
            "preview_job_id": "job",
            "preview_requested_at": time.time(),
            "preview": {"status": "pending", "kind": "pages"},
        }
        db.commit()
    data = b"page data"
    key = f"derived/{fid}/preview/p-2.jpg"
    get_storage().put_bytes(key, data, "image/jpeg")
    body = {
        "job_id": "job",
        "name": "p-2.jpg",
        "page_count": 3,
        "asset": {"key": key, "content_type": "image/jpeg", "sha256": hashlib.sha256(data).hexdigest()},
    }
    url = f"/internal/files/{fid}/preview-page"
    assert api.post(url, headers=identities["worker"], json=body).status_code == 403
    assert api.post(url, headers=identities["broker"], json={**body, "job_id": "stale"}).status_code == 403
    assert api.post(url, headers=identities["broker"], json=body).status_code == 200
    preview = api.get(f"/v1/files/{fid}/preview", headers=AUTH).json()
    assert preview["status"] == "pending" and preview["pages"] == ["p-2.jpg"]
    assert api.get(f"/v1/files/{fid}/preview/p-2.jpg", headers=AUTH).content == data
    with SessionLocal() as db:
        assert db.get(RenderedPage, (fid, 2, "poppler-1280-v1")) is not None


def test_workbook_page_publication_records_a_version_bound_sheet_outline(api, identities):  # noqa: F811
    response = api.post("/v1/files", headers=AUTH, files={"upload": ("book.pdf", b"pdf", "application/pdf")})
    fid = response.json()["id"]
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        file.status = "ready"
        file.meta = {**(file.meta or {}), "preview_job_id": "job", "preview": {"status": "pending", "kind": "pages"}}
        db.commit()
    data = b"page data"
    key = f"derived/{fid}/preview/p-1.png"
    get_storage().put_bytes(key, data, "image/png")
    body = {
        "job_id": "job",
        "name": "p-1.png",
        "page_count": 4,
        "asset": {"key": key, "content_type": "image/png", "sha256": hashlib.sha256(data).hexdigest()},
    }
    url = f"/internal/files/{fid}/preview-page"
    for starts in ([{"sheet": "A", "first_page": 2}, {"sheet": "B", "first_page": 2}], [{"sheet": "A", "first_page": 5}]):
        assert api.post(url, headers=identities["broker"], json={**body, "sheet_pages": starts}).status_code == 422
    starts = [{"sheet": "Revenue", "first_page": 1}, {"sheet": "Scanned", "first_page": 3}]
    assert api.post(url, headers=identities["broker"], json={**body, "sheet_pages": starts}).status_code == 200
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["render_sheet_pages"] == {"version": file.sha256, "page_count": 4, "sheets": starts}
        from ailab_platform.api.document_visual import sheet_mapping

        file.filename = "book.xlsx"
        assert sheet_mapping(file) == [{"sheet": "Revenue", "pages": [1, 2]}, {"sheet": "Scanned", "pages": [3, 4]}]
        file.sha256 = "changed"
        assert sheet_mapping(file) is None
