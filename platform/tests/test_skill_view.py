"""Read-only skill directory view: built-in folders, md skills, and zips unpacked once by the Worker."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from ailab_platform.agent.prompts import skills
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import DerivedAsset, DerivedBudget, FileObject, OutboxMessage
from ailab_platform.storage import get_storage
from ailab_platform.worker import skill_unpack
from ailab_platform.worker.isolate import IsolationError

from .test_personal_skills import SKILL_MD, _upload, _zip
from .test_skill_settings import SERVICE, _user

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class WorkerClient:
    """The Worker's PlatformClient for one claimed attempt, served by the test client."""

    def __init__(self, c: TestClient, token: str) -> None:
        self.c, self.headers = c, {**SERVICE, "X-Run-Attempt": token}

    def _post(self, path, body):
        response = self.c.post(path, headers=self.headers, json=body)
        response.raise_for_status()
        return response.json()

    def skill_view_action(self, file_id, action, **body):
        return self._post(f"/internal/skill-views/{file_id}/{action}", body)

    def reserve_derived(self, **body):
        return self._post("/internal/derived-assets/reserve", body)

    def finish_derived(self, key, token):
        return self._post("/internal/derived-assets/finish", {"key": key, "token": token})

    def abandon_derived_writer(self, job_id):
        return self._post("/internal/derived-assets/abandon-writer", {"job_id": job_id})

    def discard_derived_batch(self, items):
        return self._post("/internal/derived-assets/discard-batch", {"items": items})


def _derived_ready():
    from ailab_platform.api.derived_assets import backfill_batch

    with SessionLocal() as db:
        for _ in range(100):
            backfill_batch(db, get_storage())
            db.commit()
            if db.get(DerivedBudget, 1, populate_existing=True).backfill_complete:
                return
    raise AssertionError("derived accounting did not initialise")


def _jobs(file_id):
    with SessionLocal() as db:
        return [
            job.id
            for job in db.scalars(select(OutboxMessage).where(OutboxMessage.payload["file_id"].as_string() == file_id))
            if job.payload["kind"] == "skill_view"
        ]


def _run_job(c, file_id):
    (job_id,) = _jobs(file_id)
    claim = c.post(f"/internal/jobs/{job_id}/claim", headers=SERVICE, json={"consumer": "test"}).json()
    assert claim["state"] == "claimed"
    skill_unpack.process_skill_view(WorkerClient(c, claim["token"]), file_id, job_id, claim["token"])
    assert c.post(f"/internal/jobs/{job_id}/ack", headers=SERVICE, json={"token": claim["token"]}).status_code == 200


def test_builtin_skill_lists_and_reads_its_folder_without_escaping_it():
    with TestClient(app) as c:
        me = _user(c)
        name = "pptx"
        root = skills()[name]["path"].parent
        listed = c.get(f"/v1/skills/{name}/files", headers=me).json()
        assert listed["status"] == "ready"
        paths = [f["path"] for f in listed["files"]]
        assert paths == sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) and "SKILL.md" in paths
        assert set(listed["files"][0]) == {"path", "size", "kind"}

        response = c.get(f"/v1/skills/{name}/files/SKILL.md", headers=me)
        assert response.status_code == 200 and response.content == (root / "SKILL.md").read_bytes()
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-disposition"] == "inline; filename*=UTF-8''SKILL.md"
        assert c.get(f"/v1/skills/{name}/files/SKILL.md", headers={**me, "If-None-Match": response.headers["etag"]}).status_code == 304

        for bad in ("%2E%2E/%2E%2E/README.md", f"%2E%2E/{name}/SKILL.md", "a%5Cb", "scripts//x", "%2Fetc/passwd"):
            assert c.get(f"/v1/skills/{name}/files/{bad}", headers=me).status_code == 400, bad
        assert c.get(f"/v1/skills/{name}/files/missing.txt", headers=me).status_code == 404
        assert c.get("/v1/skills/no-such-skill/files", headers=me).status_code == 404


def test_md_skill_is_one_file_and_only_its_owner_sees_it():
    with TestClient(app) as c:
        me, other = _user(c), _user(c)
        name = "view-md"
        text = SKILL_MD.format(name=name).encode()
        _upload(c, me, "skill.md", text)
        listed = c.get(f"/v1/skills/{name}/files", headers=me).json()
        assert listed == {"status": "ready", "files": [{"path": "SKILL.md", "size": len(text), "kind": "text"}]}
        assert c.get(f"/v1/skills/{name}/files/SKILL.md", headers=me).content == text
        assert c.get(f"/v1/skills/{name}/files/other.txt", headers=me).status_code == 404
        assert c.get(f"/v1/skills/{name}/files", headers=other).status_code == 404
        assert c.get(f"/v1/skills/{name}/files/SKILL.md", headers=other).status_code == 404


def test_zip_skill_is_unpacked_once_by_the_worker_then_served_from_derived_cache():
    _derived_ready()
    with TestClient(app) as c:
        me, other = _user(c), _user(c)
        name = "view-zip"
        files = {
            "SKILL.md": SKILL_MD.format(name=name).encode(),
            "scripts/fill.py": b"print(1)\n",
            "scripts/copy.py": b"print(1)\n",
            "assets/logo.png": PNG,
            "data.bin": b"\x00\xff\x00",
            "page.html": b"<script>alert(1)</script>",
        }
        fid = _upload(c, me, "skill.zip", _zip(files))
        url = f"/v1/skills/{name}/files"
        assert c.get(url, headers=me).json() == {"status": "preparing"}
        assert c.get(url, headers=me).json() == {"status": "preparing"}
        assert len(_jobs(fid)) == 1  # concurrent views share one live job
        assert c.get(f"{url}/SKILL.md", headers=me).status_code == 404
        assert c.get(url, headers=other).status_code == 404

        _run_job(c, fid)
        listed = c.get(url, headers=me).json()
        assert listed["status"] == "ready"
        assert listed["files"] == [
            {"path": "SKILL.md", "size": len(files["SKILL.md"]), "kind": "text"},
            {"path": "assets/logo.png", "size": len(PNG), "kind": "image"},
            {"path": "data.bin", "size": 3, "kind": "binary"},
            {"path": "page.html", "size": len(files["page.html"]), "kind": "text"},
            {"path": "scripts/copy.py", "size": 9, "kind": "text"},
            {"path": "scripts/fill.py", "size": 9, "kind": "text"},
        ]
        assert _jobs(fid) == []

        script = c.get(f"{url}/scripts/fill.py", headers=me)
        assert script.content == b"print(1)\n" and script.headers["content-type"] == "text/plain; charset=utf-8"
        image = c.get(f"{url}/assets/logo.png", headers=me)
        assert image.content == PNG and image.headers["content-type"] == "image/png"
        assert image.headers["content-disposition"].startswith("inline")
        for path in ("data.bin", "page.html"):
            raw = c.get(f"{url}/{path}", headers=me)
            assert raw.content == files[path] and raw.headers["content-type"] == "application/octet-stream"
            assert raw.headers["content-disposition"] == f"attachment; filename*=UTF-8''{path}"
        assert c.get(f"{url}/scripts/fill.py", headers=other).status_code == 404
        assert c.get(f"{url}/scripts/%2E%2E/SKILL.md", headers=me).status_code == 400

        with SessionLocal() as db:
            view = db.get(FileObject, fid).meta["skill_view"]
            blobs = {f["blob"] for f in view["files"]}
            assert len(blobs) == 5 and all(b.startswith(f"derived/{fid}/skill/") for b in blobs)
            assets = db.scalars(select(DerivedAsset).where(DerivedAsset.key.in_(blobs))).all()
            assert len(assets) == 5 and {a.state for a in assets} == {"ready"}
        assert c.get(url, headers=me).json() == listed and _jobs(fid) == []  # a ready manifest is never recomputed

        # Collecting one cached object retires the manifest, so the next view unpacks again instead of listing it.
        from ailab_platform.api.derived_invalidation import invalidate

        with SessionLocal() as db:
            invalidate(db, db.get(DerivedAsset, sorted(blobs)[0]))
            db.commit()
        assert c.get(f"{url}/scripts/fill.py", headers=me).status_code == 404
        assert c.get(url, headers=me).json() == {"status": "preparing"} and len(_jobs(fid)) == 1


def test_failed_unpack_is_reported_and_not_retried(monkeypatch):
    def broken(*args, **kwargs):
        raise IsolationError("corrupt")

    with TestClient(app) as c:
        me = _user(c)
        name = "view-broken"
        fid = _upload(c, me, "skill.zip", _zip({"SKILL.md": SKILL_MD.format(name=name).encode()}))
        assert c.get(f"/v1/skills/{name}/files", headers=me).json() == {"status": "preparing"}
        monkeypatch.setattr(skill_unpack, "execute", broken)
        _run_job(c, fid)
        failed = {"status": "failed", "error": "技能压缩包无法展开"}
        assert c.get(f"/v1/skills/{name}/files", headers=me).json() == failed
        assert c.get(f"/v1/skills/{name}/files", headers=me).json() == failed
        assert _jobs(fid) == []  # the archive is immutable: the same bytes would fail the same way


def test_transient_failure_is_restaged_only_after_the_retry_interval():
    from datetime import UTC, datetime, timedelta

    with TestClient(app) as c:
        me = _user(c)
        name = "view-retry"
        fid = _upload(c, me, "skill.zip", _zip({"SKILL.md": SKILL_MD.format(name=name).encode()}))
        assert c.get(f"/v1/skills/{name}/files", headers=me).json() == {"status": "preparing"}
        (job_id,) = _jobs(fid)
        claim = c.post(f"/internal/jobs/{job_id}/claim", headers=SERVICE, json={"consumer": "test"}).json()
        body = {"job_id": job_id, "token": claim["token"], "error": "storage_unavailable"}
        assert c.post(f"/internal/skill-views/{fid}/complete", headers=SERVICE, json=body).json() == {"ok": False}
        assert c.post(f"/internal/skill-views/{fid}/complete", headers=SERVICE, json=body).status_code == 409
        c.post(f"/internal/jobs/{job_id}/ack", headers=SERVICE, json={"token": claim["token"]})
        assert c.get(f"/v1/skills/{name}/files", headers=me).json()["status"] == "failed"
        assert _jobs(fid) == []
        with SessionLocal() as db:
            file = db.get(FileObject, fid)
            past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
            file.meta = {**file.meta, "skill_view": {**file.meta["skill_view"], "retry_at": past}}
            db.commit()
        assert c.get(f"/v1/skills/{name}/files", headers=me).json() == {"status": "preparing"}
        assert len(_jobs(fid)) == 1
