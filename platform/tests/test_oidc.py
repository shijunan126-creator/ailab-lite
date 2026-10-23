"""Real local HTTP issuer + RSA signatures; no upstream token is returned to a Portal browser."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from ailab_platform.accounts import LoginThrottle
from ailab_platform.adapters.oidc import OIDCIdentityProvider
from ailab_platform.api import routes_auth
from ailab_platform.api.main import app
from ailab_platform.config import Settings, settings
from ailab_platform.db import SessionLocal
from ailab_platform.models import AuditLog, Department, OIDCFlow, User, utcnow


class FakeIssuer:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "first"
        self.codes = {}
        self.jwks_calls = self.token_calls = 0
        self.overrides = {}
        self.omit = []
        self.serve_keys = None
        issuer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def send_json(self, data, status=200):
                raw = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path.endswith("openid-configuration"):
                    self.send_json(
                        {
                            "issuer": issuer.url,
                            "authorization_endpoint": issuer.url + "/authorize",
                            "token_endpoint": issuer.url + "/token",
                            "jwks_uri": issuer.url + "/jwks",
                        }
                    )
                elif self.path == "/jwks":
                    issuer.jwks_calls += 1
                    key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(issuer.key.public_key()))
                    key.update(kid=issuer.kid, use="sig", alg="RS256")
                    self.send_json({"keys": issuer.serve_keys if issuer.serve_keys is not None else [key]})
                else:
                    self.send_json({}, 404)

            def do_POST(self):
                issuer.token_calls += 1
                body = parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
                params = issuer.codes.pop(body.get("code", [""])[0], None)
                verifier = body.get("code_verifier", [""])[0]
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
                if not params or challenge != params["code_challenge"][0] or body["redirect_uri"][0] != params["redirect_uri"][0]:
                    self.send_json({"error": "invalid_grant"}, 400)
                    return
                now = utcnow()
                claims = {
                    "iss": issuer.url,
                    "sub": "stable-subject",
                    "aud": "test-client",
                    "iat": now.timestamp(),
                    "exp": (now + timedelta(minutes=5)).timestamp(),
                    "nonce": params["nonce"][0],
                    "name": "Fake User",
                    "groups": ["/pilot"],
                    **issuer.overrides,
                }
                for key in issuer.omit:
                    claims.pop(key, None)
                token = jwt.encode(claims, issuer.key, algorithm="RS256", headers={"kid": issuer.kid})
                self.send_json({"id_token": token, "access_token": "upstream-secret-token", "refresh_token": "upstream-secret-refresh"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def authorize(self, url):
        params = parse_qs(urlsplit(url).query)
        assert params["response_type"] == ["code"] and params["code_challenge_method"] == ["S256"]
        code = secrets.token_urlsafe(24)
        self.codes[code] = params
        return code, params["state"][0]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


@pytest.fixture
def issuer(monkeypatch):
    fake = FakeIssuer()
    for key, value in {
        "auth_backend": "both",
        "oidc_issuer": fake.url,
        "oidc_client_id": "test-client",
        "oidc_redirect_uri": "http://127.0.0.1:14770/api/auth/oidc/callback",
        "oidc_role_mapping": {},
        "oidc_department_mapping": {},
    }.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(routes_auth, "oidc_provider", OIDCIdentityProvider(settings))
    monkeypatch.setattr(routes_auth, "address_throttle", LoginThrottle(1000, 300))
    yield fake
    fake.close()


def begin(c, issuer):
    r = c.post("/v1/auth/oidc/start")
    assert r.status_code == 200, r.text
    data = r.json()
    code, state = issuer.authorize(data["authorization_url"])
    return {"code": code, "state": state, "flow_token": data["flow_token"]}


def finish(c, issuer):
    return c.post("/v1/auth/oidc/callback", json=begin(c, issuer))


def test_oidc_happy_path_stable_id_mapping_logout_and_disabled(issuer, monkeypatch):
    with TestClient(app) as c:
        with SessionLocal() as db:
            dept = Department(name="OIDC Pilot")
            db.add(dept)
            db.commit()
            did = dept.id
        monkeypatch.setattr(settings, "oidc_role_mapping", {"/pilot": ["dept_admin"]})
        monkeypatch.setattr(settings, "oidc_department_mapping", {"/pilot": did})
        first = finish(c, issuer)
        assert first.status_code == 200, first.text
        data = first.json()
        uid = data["user"]["id"]
        assert data["user"]["roles"] == ["dept_admin", "user"] and data["user"]["dept"]["id"] == did
        assert "upstream-secret" not in first.text and "id_token" not in first.text
        again = finish(c, issuer)
        assert again.json()["user"]["id"] == uid
        headers = {"Authorization": "Bearer " + data["token"]}
        assert c.get("/v1/me", headers=headers).json()["can_admin"]
        assert c.post("/v1/auth/logout", headers=headers).status_code == 200
        assert c.get("/v1/me", headers=headers).status_code == 401
        with SessionLocal() as db:
            user = db.get(User, uid)
            assert user.last_login_at is not None and user.password_hash is None
            user.disabled_at = utcnow()
            db.commit()
        assert finish(c, issuer).status_code == 401
        assert c.get("/v1/me", headers={"Authorization": "Bearer " + again.json()["token"]}).status_code == 401
        with SessionLocal() as db:
            assert len(list(db.scalars(select(User).where(User.issuer == issuer.url)))) == 1
            assert db.scalar(select(AuditLog).where(AuditLog.action == "auth.register", AuditLog.target_id == uid))


@pytest.mark.parametrize(
    "invalid", ["state", "binding", "expired_flow", "nonce", "expired_token", "issuer", "audience", "azp", "missing_exp", "error"]
)
def test_oidc_rejects_invalid_flows_and_claims(issuer, invalid):
    with TestClient(app) as c:
        body = begin(c, issuer)
        if invalid == "state":
            body["state"] = secrets.token_urlsafe(32)
        elif invalid == "binding":
            body["flow_token"] = secrets.token_urlsafe(32)
        elif invalid == "expired_flow":
            with SessionLocal() as db:
                from ailab_platform.db import hash_token

                db.get(OIDCFlow, hash_token(body["state"])).expires_at = utcnow() - timedelta(seconds=1)
                db.commit()
        elif invalid == "nonce":
            issuer.overrides["nonce"] = "wrong"
        elif invalid == "expired_token":
            issuer.overrides["exp"] = (utcnow() - timedelta(seconds=1)).timestamp()
        elif invalid == "issuer":
            issuer.overrides["iss"] = "https://other.example"
        elif invalid == "audience":
            issuer.overrides["aud"] = "other-client"
        elif invalid == "azp":
            issuer.overrides.update(aud=["test-client", "other-client"], azp="other-client")
        elif invalid == "missing_exp":
            issuer.omit = ["exp"]
        elif invalid == "error":
            body["error"] = "access_denied"
        r = c.post("/v1/auth/oidc/callback", json=body)
        assert r.status_code == 401, r.text
        assert "upstream" not in r.text
        if invalid in {"state", "binding", "expired_flow", "error"}:
            assert issuer.token_calls == 0
        with SessionLocal() as db:
            assert db.scalar(select(User).where(User.issuer == issuer.url)) is None
            assert db.scalar(select(AuditLog).where(AuditLog.action == "auth.login", AuditLog.outcome == "failure"))


def test_oidc_single_use_and_key_rotation_unknown_kid_refetch(issuer):
    with TestClient(app) as c:
        body = begin(c, issuer)
        assert c.post("/v1/auth/oidc/callback", json=body).status_code == 200
        assert issuer.jwks_calls == 1
        assert c.post("/v1/auth/oidc/callback", json=body).status_code == 401
        assert issuer.token_calls == 1
        assert finish(c, issuer).status_code == 200
        assert issuer.jwks_calls == 1
        issuer.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        issuer.kid = "rotated"
        assert finish(c, issuer).status_code == 200
        assert issuer.jwks_calls == 2
        issuer.kid = "unknown"
        issuer.serve_keys = []
        assert finish(c, issuer).status_code == 401
        assert issuer.jwks_calls == 3


def test_group_mapping_refresh_removes_roles_and_ambiguous_department_is_refused(issuer, monkeypatch):
    with TestClient(app) as c:
        monkeypatch.setattr(settings, "oidc_groups_claim", "realm.groups")
        issuer.overrides["realm"] = {"groups": ["/admins"]}
        monkeypatch.setattr(settings, "oidc_role_mapping", {"/admins": ["platform_admin"]})
        r = finish(c, issuer).json()
        assert "platform_admin" in r["user"]["roles"]
        issuer.overrides["realm"] = {"groups": []}
        assert finish(c, issuer).json()["user"]["roles"] == ["user"]
        assert not c.get("/v1/me", headers={"Authorization": "Bearer " + r["token"]}).json()["can_admin"]
        monkeypatch.setattr(settings, "oidc_department_mapping", {"/a": "a", "/b": "b"})
        issuer.overrides["realm"] = {"groups": ["/a", "/b"]}
        assert finish(c, issuer).status_code == 401


def test_login_backend_options_and_settings_validation(issuer, monkeypatch):
    with TestClient(app) as c:
        assert c.get("/v1/auth/options").json()["oidc"]["enabled"]
        monkeypatch.setattr(settings, "auth_backend", "oidc")
        assert c.post("/v1/auth/login", json={"username": "test", "password": "some-password"}).status_code == 403
        assert c.get("/v1/auth/options").json()["registration"] == "closed"
        monkeypatch.setattr(settings, "auth_backend", "local")
        assert c.post("/v1/auth/oidc/start").status_code == 404
    with pytest.raises(ValidationError):
        Settings(_env_file=None, auth_backend="invalid")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            auth_backend="oidc",
            oidc_issuer="http://idp.example",
            oidc_client_id="c",
            oidc_redirect_uri="https://portal.example/api/auth/oidc/callback",
        )


@pytest.mark.parametrize(("backend", "oidc", "password"), [("local", False, True), ("oidc", True, False), ("both", True, True)])
def test_login_options_say_which_methods_are_offered(monkeypatch, backend, oidc, password):
    from fastapi.testclient import TestClient

    from ailab_platform.api.main import app
    from ailab_platform.config import settings

    monkeypatch.setattr(settings, "auth_backend", backend)
    with TestClient(app) as client:
        body = client.get("/v1/auth/options").json()
    assert body["oidc"]["enabled"] is oidc and body["password"] is password
