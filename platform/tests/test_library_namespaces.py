"""Project folders, private skill storage, and collision-safe sibling names."""

import pytest
from fastapi.testclient import TestClient

from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject
from tests.test_conversations import _other_user
from tests.test_projects import project, upload


@pytest.fixture
def api():
    with TestClient(app) as client:
        client.headers.update(_other_user(client))
        yield client


def test_project_names_are_normalized_and_cannot_collide(api):
    one = project(api, "季度计划")
    assert api.post("/v1/projects", json={"name": " 季度计划 "}).status_code == 409
    other = project(api, "另一个项目")
    assert api.patch(f"/v1/projects/{other['id']}", json={"name": one["name"]}).status_code == 409
    project(api, "Report")
    assert api.post("/v1/projects", json={"name": "ＲＥＰＯＲＴ"}).status_code == 409


def test_sibling_upload_move_restore_and_versions_preserve_both_files(api):
    folder = project(api, "文件目录")
    first = upload(api, "报告.txt")
    second = upload(api, "报告.txt")
    assert second["filename"] == "报告 (2).txt"
    grouped = upload(api, "报告.txt", project_id=folder["id"])
    moved = api.patch(f"/v1/files/{grouped['id']}", json={"project_id": None})
    assert moved.status_code == 200, moved.text
    assert moved.json()["filename"] == "报告 (3).txt"
    assert api.delete(f"/v1/files/{first['id']}").status_code == 204
    replacement = upload(api, "报告.txt")
    restored = api.post(f"/v1/files/{first['id']}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["filename"] == "报告 (4).txt"
    version = upload(api, replacement["filename"], parent_id=replacement["id"])
    assert version["filename"] == replacement["filename"] and version["version"] == 2
    assert len(api.get("/v1/files").json()) == 4


def test_folders_respect_categories_and_never_flatten_project_files(api):
    folder = project(api, "文件夹")
    grouped = upload(api, "计划.txt", project_id=folder["id"])
    loose = upload(api, "独立.txt")
    assert [f["id"] for f in api.get("/v1/files?scope=root").json()] == [loose["id"]]
    assert api.get("/v1/files/folders").json() == [{"id": folder["id"], "name": folder["name"], "files": 1}]
    assert api.get("/v1/files/folders?category=starred").json() == []
    api.patch(f"/v1/files/{grouped['id']}", json={"starred": True})
    assert api.get("/v1/files/folders?category=starred").json()[0]["files"] == 1
    assert api.get("/v1/files/folders?q=不存在").json() == []
    api.delete(f"/v1/projects/{folder['id']}")
    assert api.get("/v1/files?scope=root&trashed=true").json() == []
    assert api.get("/v1/files/folders?trashed=true").json()[0]["name"] == folder["name"]
    assert api.get(f"/v1/files?project_id={folder['id']}&trashed=true").json()[0]["id"] == grouped["id"]


def test_skill_upload_hidden_immediately_and_storage_freed_on_delete(api):
    response = api.post(
        "/v1/files?purpose=skill", files={"upload": ("SKILL.md", b"---\nname: custom\ndescription: Test\n---\n# Skill", "text/markdown")}
    )
    assert response.status_code == 201, response.text
    skill = response.json()
    assert api.get("/v1/files").json() == []
    assert api.get(f"/v1/files?ids={skill['id']}").json() == []
    with SessionLocal() as db:
        db.get(FileObject, skill["id"]).status = "ready"
        db.commit()
    assert api.post("/v1/runs", json={"prompt": "read", "file_ids": [skill["id"]]}).status_code == 400
    assert api.get("/v1/skills/storage").json()["used_bytes"] == skill["size"]
    assert api.delete(f"/v1/skills/files/{skill['id']}").status_code == 204
    assert api.get("/v1/skills/storage").json()["used_bytes"] == 0
    with SessionLocal() as db:
        assert db.get(FileObject, skill["id"]) is None


def test_folder_pagination_empty_projects_images_and_search(api):
    folders = [project(api, f"Folder {i}") for i in range(5)]
    for folder in folders[:3]:
        upload(api, "计划.txt", project_id=folder["id"])
    found, after = [], ""
    while page := api.get("/v1/files/folders", params={"after": after, "limit": 2}).json():
        found.extend(page)
        after = page[-1]["id"]
    assert len(found) == 5 and {f["id"] for f in found} == {f["id"] for f in folders}
    assert sum(f["files"] for f in found) == 3
    assert len(api.get("/v1/files/folders?q=计划").json()) == 3
    assert api.get("/v1/files/folders?category=images").json() == []
    image = api.post("/v1/files", params={"project_id": folders[0]["id"]}, files={"upload": ("photo.png", b"bytes", "image/png")}).json()
    assert api.get("/v1/files/folders?category=images").json() == [{"id": folders[0]["id"], "name": "Folder 0", "files": 1}]
    api.delete(f"/v1/files/{image['id']}")
    assert api.get("/v1/files/folders?category=images").json() == []
    assert api.get("/v1/files/folders?category=images&trashed=true").json()[0]["files"] == 1


def test_concurrent_sibling_uploads_and_project_creation(api):
    from concurrent.futures import ThreadPoolExecutor

    def send(kind):
        client = TestClient(app, headers=dict(api.headers))
        if kind == "file":
            return client.post("/v1/files", files={"upload": ("并发.txt", b"preserved", "text/plain")})
        return client.post("/v1/projects", json={"name": "并发项目"})

    with ThreadPoolExecutor(max_workers=3) as pool:
        files = list(pool.map(send, ["file"] * 3))
    assert all(r.status_code == 201 for r in files)
    assert {r.json()["filename"] for r in files} == {"并发.txt", "并发 (2).txt", "并发 (3).txt"}
    ids = {r.json()["id"] for r in files}
    assert len(ids) == 3
    with SessionLocal() as db:
        assert len({db.get(FileObject, fid).storage_key for fid in ids}) == 3
    with ThreadPoolExecutor(max_workers=2) as pool:
        projects = list(pool.map(send, ["project"] * 2))
    assert sorted(r.status_code for r in projects) == [201, 409]
