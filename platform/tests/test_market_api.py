"""The legacy market is empty after office skills become platform owned."""

from fastapi.testclient import TestClient

from ailab_platform import market
from ailab_platform.api.main import app

from .test_skill_settings import _user


def test_catalog_and_legacy_install():
    assert market.listing() == []
    with TestClient(app) as client:
        me = _user(client)
        assert client.get("/v1/market/skills", headers=me).json() == []
        assert client.post("/v1/market/skills/docx/install", headers=me).status_code == 404
