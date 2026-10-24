"""Template readiness is independent of its durable sandbox preview."""

import hashlib
import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, OutboxMessage
from ailab_platform.sandbox_tools.render_preview import template_thumbnail
from ailab_platform.storage import get_storage
from ailab_platform.worker import isolate, pipeline
from tests.test_projects_brand import SERVICE, WorkerAPI
from tests.test_projects_brand import api as api
from tests.test_templates import file_id, other_user, ready


def preview_job(fid):
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        return file.meta["preview_job_id"]


def complete(api, fid, *, failed=False):
    body = {"job_id": preview_job(fid), "assets": {}}
    if failed:
        body["preview"] = {"status": "failed", "kind": "pages", "reason": "render_failed"}
    else:
        body["preview"] = {"status": "ready", "kind": "pages", "pages": ["p-01.jpg"], "page_count": 1}
        for name, size in [("p-01.jpg", (1280, 960)), ("template-thumbnail.jpg", (480, 360))]:
            data = io.BytesIO()
            Image.new("RGB", size, "#123456").save(data, "JPEG")
            key = f"derived/{fid}/preview/{name}"
            get_storage().put_bytes(key, data.getvalue(), "image/jpeg")
            body["assets"][name] = {"key": key, "content_type": "image/jpeg", "sha256": hashlib.sha256(data.getvalue()).hexdigest()}
    result = api.post(f"/internal/files/{fid}/preview", headers=SERVICE, json=body)
    assert result.status_code == 200, result.text
    return body


@pytest.mark.parametrize("potx", [False, True])
def test_ready_stages_one_preview_with_retries_and_atomic_rollback(api, potx):
    row = ready(api, name="sample.potx" if potx else "sample.pptx")
    fid = file_id(row)
    job = preview_job(fid)
    with SessionLocal() as db:
        file = db.get(FileObject, fid)
        assert file.meta["preview"] == {"status": "pending", "kind": "pages"}
        outbox = db.get(OutboxMessage, job)
        assert outbox.payload["kind"] == "preview" and outbox.payload["file_id"] == fid
    for _ in range(2):
        assert api.patch(f"/internal/files/{fid}", headers=SERVICE, json={"status": "ready"}).status_code == 200
    assert pipeline.process_file(fid, WorkerAPI(api))["skipped"] == "ready"
    with SessionLocal() as db:
        jobs = [j for j in db.scalars(select(OutboxMessage)) if j.payload.get("file_id") == fid and j.payload["kind"] == "preview"]
        assert [j.id for j in jobs] == [job]
        # Both manifest and job disappear when the caller rolls back its transaction.
        from ailab_platform.api.preview_requests import request_preview

        file = db.get(FileObject, fid)
        original = dict(file.meta)
        file.meta = {k: v for k, v in original.items() if not k.startswith("preview")}
        _, staged = request_preview(db, file, retry_stale=False)
        db.flush()
        db.rollback()
        assert db.get(OutboxMessage, staged) is None
        assert db.get(FileObject, fid).meta == original


def test_thumbnail_switches_url_and_etag_only_when_preview_ready(api):
    row = ready(api)
    fid = file_id(row)
    assert row["preview_status"] == "pending"
    sketch = api.get(row["thumbnail_url"])
    assert sketch.status_code == 200 and "immutable" in sketch.headers["cache-control"]
    body = complete(api, fid)
    fresh = api.get(f"/v1/templates/{row['id']}").json()
    assert fresh["preview_status"] == "ready"
    assert fresh["status"] == "ready" and fresh["thumbnail_url"] != row["thumbnail_url"]
    assert body["assets"]["template-thumbnail.jpg"]["sha256"] in fresh["thumbnail_url"]
    rendered = api.get(fresh["thumbnail_url"], headers={"If-None-Match": sketch.headers["etag"]})
    assert rendered.status_code == 200 and rendered.headers["etag"] != sketch.headers["etag"]
    assert rendered.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(rendered.content)) as image:
        assert image.size == (480, 360)
    assert api.get(fresh["thumbnail_url"], headers={"If-None-Match": rendered.headers["etag"]}).status_code == 304
    assert api.get(fresh["thumbnail_url"], headers=other_user()).status_code == 404
    assert api.get(f"/v1/files/{fid}/preview").status_code == 404
    # Completion retries cannot replace an already committed derivative.
    assert api.post(f"/internal/files/{fid}/preview", headers=SERVICE, json=body).status_code == 200
    assert api.get(f"/v1/templates/{row['id']}").json()["thumbnail_url"] == fresh["thumbnail_url"]


def test_failed_preview_keeps_sketch_and_template_usable(api):
    row = ready(api)
    fid = file_id(row)
    complete(api, fid, failed=True)
    assert api.patch(f"/internal/files/{fid}", headers=SERVICE, json={"status": "ready"}).status_code == 200
    fresh = api.get(f"/v1/templates/{row['id']}").json()
    assert fresh["preview_status"] == "failed"
    assert fresh["status"] == "ready" and fresh["reason"] is None
    assert fresh["thumbnail_url"] == row["thumbnail_url"]
    assert api.get(fresh["thumbnail_url"]).status_code == 200
    from ailab_platform.api.templates import freeze_template
    from ailab_platform.models import Template, User

    with SessionLocal() as db:
        tpl = db.get(Template, row["id"])
        assert freeze_template(db, db.get(User, tpl.owner_id), {"id": tpl.id}, "work")["file_id"] == fid


def test_sandbox_thumbnail_is_downscaled_first_page(tmp_path):
    Image.new("RGB", (1280, 960), "#123456").save(tmp_path / "p-01.jpg")
    Image.new("RGB", (1280, 960), "red").save(tmp_path / "p-02.jpg")
    template_thumbnail(tmp_path, {"pages": ["p-01.jpg", "p-02.jpg"]})
    with Image.open(tmp_path / "template-thumbnail.jpg") as image:
        assert image.format == "JPEG" and image.size == (480, 360)
        assert max(abs(a - b) for a, b in zip(image.getpixel((10, 10)), (18, 52, 86))) < 4


def test_sketch_failure_preserves_template_analysis(api, monkeypatch):
    original = pipeline.execute

    def execute(operation, *args, **kwargs):
        if operation == "template_sketch":
            raise isolate.IsolationError("memory_limit")
        return original(operation, *args, **kwargs)

    monkeypatch.setattr(pipeline, "execute", execute)
    row = ready(api)
    assert row["status"] == "ready" and row["thumbnail_url"] is None
    complete(api, file_id(row))
    fresh = api.get(f"/v1/templates/{row['id']}").json()
    assert fresh["thumbnail_url"] and api.get(fresh["thumbnail_url"]).status_code == 200


def test_platform_image_has_no_office_renderer():
    dockerfile = (Path(__file__).resolve().parents[1] / "deploy/Dockerfile").read_text().lower()
    assert all(package not in dockerfile for package in ("libreoffice", "poppler", "fonts-noto", "fontconfig", "procps"))
