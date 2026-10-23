"""Optional Keycloak realm acceptance; requires a configured confidential/public test client and user.

AILAB_TEST_OIDC_ISSUER, _CLIENT_ID, _CLIENT_SECRET (optional), _REDIRECT_URI, _USERNAME, _PASSWORD.
Only use a disposable test realm. The test disables its JIT account in the isolated platform DB.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ailab_platform.adapters.oidc import OIDCIdentityProvider
from ailab_platform.api import routes_auth
from ailab_platform.api.main import app
from ailab_platform.config import settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import User, utcnow


@pytest.mark.skipif(not os.getenv("AILAB_TEST_OIDC_ISSUER"), reason="AILAB_TEST_OIDC_ISSUER not set (disposable Keycloak realm)")
def test_keycloak_code_login_logout_and_disabled(monkeypatch):
    from playwright.sync_api import sync_playwright

    issuer = os.environ["AILAB_TEST_OIDC_ISSUER"]
    redirect = os.getenv("AILAB_TEST_OIDC_REDIRECT_URI", "http://127.0.0.1:14770/api/auth/oidc/callback")
    for key, value in {
        "auth_backend": "both",
        "oidc_issuer": issuer,
        "oidc_client_id": os.getenv("AILAB_TEST_OIDC_CLIENT_ID", "ailab-test"),
        "oidc_client_secret": SecretStr(os.getenv("AILAB_TEST_OIDC_CLIENT_SECRET", "")),
        "oidc_redirect_uri": redirect,
    }.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(routes_auth, "oidc_provider", OIDCIdentityProvider(settings))
    username = os.environ["AILAB_TEST_OIDC_USERNAME"]
    password = os.environ["AILAB_TEST_OIDC_PASSWORD"]
    with TestClient(app) as c, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            start = c.post("/v1/auth/oidc/start").json()
            callback_urls = []

            def capture(route):
                callback_urls.append(route.request.url)
                route.fulfill(status=200, body="Callback received")

            page.route(redirect + "*", capture)
            page.goto(start["authorization_url"])
            page.locator("#username").fill(username)
            page.locator("#password").fill(password)
            page.locator('button[type="submit"], input[type="submit"]').first.click()
            page.wait_for_url(redirect + "**", timeout=30000)
            params = parse_qs(urlsplit(callback_urls[0]).query)
            result = c.post(
                "/v1/auth/oidc/callback", json={"state": params["state"][0], "code": params["code"][0], "flow_token": start["flow_token"]}
            )
            assert result.status_code == 200, result.text
            data = result.json()
            assert "access_token" not in data and "id_token" not in data
            headers = {"Authorization": "Bearer " + data["token"]}
            assert c.get("/v1/me", headers=headers).status_code == 200
            assert c.post("/v1/auth/logout", headers=headers).status_code == 200
            assert c.get("/v1/me", headers=headers).status_code == 401
            with SessionLocal() as db:
                db.get(User, data["user"]["id"]).disabled_at = utcnow()
                db.commit()
            # The IdP browser session is still valid; the platform must independently refuse this user.
            start = c.post("/v1/auth/oidc/start").json()
            callback_urls.clear()
            page.goto(start["authorization_url"])
            page.wait_for_url(redirect + "**", timeout=30000)
            params = parse_qs(urlsplit(callback_urls[0]).query)
            assert (
                c.post(
                    "/v1/auth/oidc/callback",
                    json={"state": params["state"][0], "code": params["code"][0], "flow_token": start["flow_token"]},
                ).status_code
                == 401
            )
        finally:
            browser.close()
