"""Curated business skills ship as valid packages and install through personal-skill governance."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from ailab_platform import market, skillfiles
from ailab_platform.api.main import app
from ailab_platform.db import SessionLocal
from ailab_platform.models import FileObject, FileStatus
from ailab_platform.storage import get_storage

from .test_skill_settings import _user


def test_catalog_and_package_integrity() -> None:
    items = market.listing()
    assert len(items) == 8
    assert len({item["slug"] for item in items}) == len(items)
    assert len({item["name"] for item in items}) == len(items)
    for item in items:
        assert item["builtin"] is False
        assert item["author"] == "AILab"
        assert item["license"] == "Apache-2.0"
        assert item["version"] == "1.0.0"
        assert isinstance(item["requires"], list)
        assert item["category"]
        assert item["name"] not in {"docx", "pdf", "pptx", "xlsx"}
        data = market.package_zip(item["slug"])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            assert "SKILL.md" in archive.namelist()
            meta = skillfiles.frontmatter(archive.read("SKILL.md").decode("utf-8"))
            for key in ("name", "title", "description"):
                assert meta[key] == item[key]
        scanned = skillfiles.inspect(data, f"{item['slug']}.zip")
        assert scanned is not None and scanned["name"] == item["name"]


def test_market_installs_pending_personal_copy() -> None:
    item = market.listing()[0]
    slug = item["slug"]
    with TestClient(app) as client:
        me, other = _user(client), _user(client)
        listed = client.get("/v1/market/skills", headers=me)
        assert listed.status_code == 200
        assert len(listed.json()) == len(market.CATALOG)
        assert all(not entry["installed"] for entry in listed.json())
        assert client.post(f"/v1/market/skills/{slug}/install").status_code == 401
        installed = client.post(f"/v1/market/skills/{slug}/install", headers=me)
        assert installed.status_code == 201
        body = installed.json()
        assert body["name"] == item["name"] and body["status"] == "pending"
        with SessionLocal() as db:
            row = db.get(FileObject, body["file_id"])
            assert row.purpose == "skill" and row.is_head == 1
            assert row.status == FileStatus.PENDING
            assert row.meta == {
                "processing": "queued",
                "skill_claim": item["name"],
                "source": "market",
                "market_version": item["version"],
            }
            assert skillfiles.inspect(get_storage().get_bytes(row.storage_key), row.filename)["name"] == item["name"]
        pending = client.get(f"/v1/files/{body['file_id']}", headers=me)
        assert pending.status_code == 200 and pending.json()["status"] == "pending"
        assert client.get(f"/v1/files/{body['file_id']}", headers=other).status_code == 404
        assert client.post(f"/v1/market/skills/{slug}/install", headers=me).status_code == 409
        assert client.post(f"/v1/market/skills/{slug}/install", headers=other).status_code == 201
        assert client.post("/v1/market/skills/docx/install", headers=me).status_code == 404
