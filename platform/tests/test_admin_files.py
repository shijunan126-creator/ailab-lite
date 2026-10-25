"""Admin file provenance: metadata search and version uploader."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from ailab_platform import accounts
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal, init_db
from ailab_platform.library import assign_file_version
from ailab_platform.models import FileObject, FileStatus, UserRole, new_id
from ailab_platform.storage import get_storage


def _user(c: TestClient, *, admin: bool = False) -> tuple[dict, str]:
    init_db()
    name = f"f{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        user = accounts.create_user(db, name, "provenance-password")
        if admin:
            db.add(UserRole(user_id=user.id, role="platform_admin"))
        db.commit()
        user_id = user.id
    token = c.post("/v1/auth/login", json={"username": name, "password": "provenance-password"}).json()["token"]
    return {"Authorization": f"Bearer {token}"}, user_id


def test_admin_can_find_file_uploader_and_version_history() -> None:
    with TestClient(app) as c:
        admin, _ = _user(c, admin=True)
        owner, owner_id = _user(c)
        content = b"provenance"
        file_id = new_id("fil")
        key = f"library/{owner_id}/{file_id}/report.txt"
        get_storage().put_bytes(key, content, "text/plain")
        with SessionLocal() as db:
            file = FileObject(
                id=file_id,
                user_id=owner_id,
                filename="report.txt",
                content_type="text/plain",
                size=len(content),
                sha256="abc",
                storage_key=key,
                status=FileStatus.READY,
                purpose="library",
            )
            assign_file_version(db, file, None)
            db.add(file)
            db.commit()

        found = c.get("/v1/admin/files?q=report", headers=admin)
        assert found.status_code == 200
        items = found.json()["items"]
        assert any(item["id"] == file_id and item["uploader"]["id"] == owner_id for item in items)

        detail = c.get(f"/v1/admin/files/{file_id}", headers=admin)
        assert detail.status_code == 200
        assert detail.json()["versions"][0]["uploader"]["id"] == owner_id
        assert c.get("/v1/admin/files?q=report", headers=owner).status_code == 403
