import hashlib
import time

from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, RenderedPage, Run
from ailab_platform.storage import get_storage
from tests.test_ledger_protocol import AUTH, api  # noqa: F401
from tests.test_service_identity import identities  # noqa: F401


def test_page_commit_is_job_bound_visible_and_wakes_matching_waiter(api, identities):  # noqa: F811
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
    run = api.post("/v1/runs", headers=AUTH, json={"prompt": "看第 2 页", "file_ids": [fid]}).json()
    with SessionLocal() as db:
        row = db.get(Run, run["id"])
        row.status = "interrupted"
        row.runtime_meta = {
            **row.runtime_meta,
            "file_dependency": {
                "file_id": fid,
                "action": "image-reference",
                "pages": [2],
                "deadline": time.time() + 100,
            },
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
        assert db.get(Run, run["id"]).status == "queued"
