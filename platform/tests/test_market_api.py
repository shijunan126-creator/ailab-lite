"""Official market: catalog, package integrity, and install as a personal skill."""

from __future__ import annotations

import io
import zipfile

from fastapi.testclient import TestClient

from ailab_platform import market
from ailab_platform.api.main import app

from .test_skill_settings import _user


def test_catalog_and_package_integrity() -> None:
    items = {item["slug"]: item for item in market.listing()}
    assert {"docx", "pdf", "pptx", "xlsx"} <= set(items)
    assert items["docx"]["builtin"] is False
    assert items["pdf"]["builtin"] is False
    for slug in ("docx", "pdf"):
        with zipfile.ZipFile(io.BytesIO(market.package_zip(slug))) as archive:
            names = set(archive.namelist())
            assert "SKILL.md" in names
            assert any(name.startswith("scripts/") for name in names)


def test_market_lists_official_skills_and_installs_personal_copy() -> None:
    with TestClient(app) as c:
        me = _user(c)
        listed = c.get("/v1/market/skills", headers=me).json()
        assert any(item["name"] == "docx" and not item["installed"] for item in listed)
        assert next(item for item in listed if item["name"] == "pptx")["builtin"] is True

        installed = c.post("/v1/market/skills/docx/install", headers=me)
        assert installed.status_code == 201
        body = installed.json()
        assert body["name"] == "docx" and body["status"] == "pending"

        again = c.post("/v1/market/skills/docx/install", headers=me)
        assert again.status_code == 409
        builtin = c.post("/v1/market/skills/pptx/install", headers=me)
        assert builtin.status_code == 409
        pending = c.get(f"/v1/files/{body['file_id']}", headers=me)
        assert pending.status_code == 200
        assert pending.json()["status"] == "pending"
