"""Security boundaries deny requests before touching a file or another service."""

import hashlib

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.requests import Request

from ailab_platform.config import Settings, settings
from ailab_platform.security import client_address
from ailab_platform.service_auth import assert_internal_scopes


@pytest.mark.parametrize("field", ["sandbox_provider", "storage_backend", "queue_backend", "image_search_provider", "scanner_backend"])
def test_unknown_backend_rejected(field):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: "typo"})


@pytest.mark.parametrize(
    "field,value", [("max_sandboxes", -1), ("llm_timeout_seconds", 0), ("max_upload_bytes", 0), ("cached_token_weight", 1.1)]
)
def test_invalid_limits_rejected(field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


@pytest.mark.parametrize("included", [False, True])
def test_route_guard_detects_missing_scope(included):
    app = FastAPI()
    router = APIRouter()

    @router.get("/forgotten")
    def forgotten():
        return {}

    if included:
        app.include_router(router, prefix="/internal")
    else:
        app.add_api_route("/internal/forgotten", forgotten)
    with pytest.raises(AssertionError, match="forgotten"):
        assert_internal_scopes(app)


def test_all_service_internal_routes_are_scoped():
    from ailab_platform.agent.main import app as agent
    from ailab_platform.api.main import app as api
    from ailab_platform.broker.main import app as broker
    from ailab_platform.worker.main import app as worker

    for app in (api, broker, agent, worker):
        assert_internal_scopes(app)


def test_untrusted_forwarding_ignored(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxies", ["10.0.0.0/8"])

    def request(peer, forwarded):
        return Request({"type": "http", "client": (peer, 123), "headers": [(b"x-forwarded-for", forwarded.encode())]})

    assert client_address(request("203.0.113.1", "1.2.3.4")) == "203.0.113.1"
    assert client_address(request("10.0.0.2", "1.2.3.4, 203.0.113.2, 10.0.0.1")) == "203.0.113.2"
    assert client_address(request("10.0.0.2", "garbage")) == "10.0.0.2"


@pytest.fixture
def identities(monkeypatch):
    tokens = {name: f"unique-{name}-credential-{'x' * 32}" for name in ("api", "agent", "broker", "worker", "ops")}
    monkeypatch.setattr(
        settings, "service_credentials", {name: "sha256:" + hashlib.sha256(token.encode()).hexdigest() for name, token in tokens.items()}
    )
    return {name: {"X-Service-Token": token} for name, token in tokens.items()}


def test_service_scope_and_file_binding(identities):
    from ailab_platform.api.main import app
    from ailab_platform.db import SessionLocal, init_db
    from ailab_platform.models import FileObject, FileStatus, Run, RunStatus, User, new_id

    init_db()
    with SessionLocal() as db:
        a, b = User(id=new_id("test"), email=f"{new_id('test')}@test"), User(id=new_id("test"), email=f"{new_id('test')}@test")
        db.add_all([a, b])
        db.flush()
        own = FileObject(id=new_id("test"), user_id=a.id, filename="own.txt", storage_key="own", status=FileStatus.READY, size=1)
        other = FileObject(id=new_id("test"), user_id=b.id, filename="other.txt", storage_key="other", status=FileStatus.READY, size=1)
        detached = FileObject(
            id=new_id("test"), user_id=a.id, filename="detached.txt", storage_key="detached", status=FileStatus.READY, size=1
        )
        run = Run(id=new_id("test"), user_id=a.id, prompt="test", model="mock", status=RunStatus.RUNNING, input_file_ids=[own.id])
        db.add_all([own, other, detached, run])
        db.commit()
        ids = own.id, other.id, detached.id, run.id
    own_id, other_id, detached_id, run_id = ids
    with TestClient(app) as client:
        assert client.get(f"/internal/runs/{run_id}", headers=identities["worker"]).status_code == 403
        assert client.post(f"/internal/runs/{run_id}/usage", headers=identities["broker"], json={}).status_code == 403
        assert client.patch(f"/internal/files/{own_id}", headers=identities["agent"], json={"status": "rejected"}).status_code == 403
        for name in ("agent", "broker"):
            assert client.get(f"/internal/files/{own_id}", headers=identities[name]).status_code == 403
            assert client.get(f"/internal/files/{own_id}?run_id={run_id}", headers=identities[name]).status_code == 200
            for denied in (other_id, detached_id):
                assert client.get(f"/internal/files/{denied}?run_id={run_id}", headers=identities[name]).status_code in (403, 404)
        assert (
            client.patch(f"/internal/files/{own_id}", headers=identities["worker"], json={"meta": {"scan": {"ok": False}}}).status_code
            == 403
        )
        assert (
            client.patch(
                f"/internal/files/{own_id}?job=thumbnail",
                headers=identities["worker"],
                json={"meta": {"thumb_key": f"derived/{own_id}/thumb.webp"}},
            ).status_code
            == 200
        )
        assert (
            client.patch(f"/internal/files/{own_id}?job=thumbnail", headers=identities["worker"], json={"status": "rejected"}).status_code
            == 403
        )


def test_render_owner_is_only_returned_after_job_binding(identities):
    from ailab_platform.api.main import app
    from ailab_platform.db import SessionLocal, init_db
    from ailab_platform.models import FileObject, FileStatus, User, new_id

    init_db()
    with SessionLocal() as db:
        user = User(id=new_id("test"), email=f"{new_id('test')}@test")
        db.add(user)
        db.flush()
        file = FileObject(
            id=new_id("test"),
            user_id=user.id,
            filename="own.pdf",
            storage_key="own",
            status=FileStatus.READY,
            size=1,
            meta={"preview_job_id": "job", "preview": {"status": "pending"}},
        )
        db.add(file)
        db.commit()
        file_id, user_id = file.id, user.id
    with TestClient(app) as client:
        url = f"/internal/files/{file_id}"
        valid = client.get(url, params={"job": "preview", "preview_job": "job"}, headers=identities["broker"])
        assert valid.status_code == 200 and valid.json()["render_owner_id"] == user_id
        assert client.get(url, params={"job": "preview", "preview_job": "wrong"}, headers=identities["broker"]).status_code == 403
        worker = client.get(url, params={"job": "preview"}, headers=identities["worker"])
        assert worker.status_code == 200 and "render_owner_id" not in worker.json()


def secure_settings(**overrides):
    tokens = {name: f"operator-{name}-{'z' * 32}" for name in ("api", "agent", "broker", "worker", "ops")}
    values = dict(
        profile="secure",
        service_token="",
        service_name="api",
        service_credential=tokens["api"],
        service_credentials={name: "sha256:" + hashlib.sha256(token.encode()).hexdigest() for name, token in tokens.items()},
        dev_token="",
        osb_api_key="",
        sandbox_provider="docker",
        scanner_backend="clamav",
        registration="closed",
    )
    return Settings(_env_file=None, **(values | overrides))


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"sandbox_provider": "mock"}, "mock sandbox"),
        ({"scanner_backend": "stub"}, "stub scanner"),
        ({"dev_token": "dev-demo-token"}, "DEV_TOKEN"),
        ({"osb_api_key": "dev-osb-key"}, "OSB_API_KEY"),
        ({"service_token": "dev-service-token"}, "SERVICE_TOKEN"),
        ({"service_token": "custom-legacy-token"}, "legacy"),
        ({"registration": "open", "invite_code": ""}, "invite code"),
        ({"service_credential": "short"}, "24 characters"),
        ({"service_credentials": {}}, "all five"),
    ],
)
def test_secure_profile_rejects_unsafe_defaults(overrides, reason):
    with pytest.raises(RuntimeError, match=reason):
        secure_settings(**overrides).validate_secrets()


def test_secure_profile_and_external_catalog(tmp_path):
    catalog = tmp_path / "models.toml"
    catalog.write_text('[[models]]\nid="external"\nbase_url="https://example.com/v1"\n')
    with pytest.raises(RuntimeError, match="external host"):
        secure_settings(models_file=catalog).validate_secrets()
    secure_settings(models_file=catalog, allow_external_models=True).validate_secrets()
    for host in ("127.0.0.1", "10.2.3.4", "[::1]", "higress.gateway.svc.cluster.local"):
        catalog.write_text(f'[[models]]\nid="local"\nbase_url="http://{host}/v1"\n')
        secure_settings(models_file=catalog).validate_secrets()


@pytest.mark.parametrize("env,profile", [("dev", "dev"), ("smoke", "dev"), ("k3s", "dev"), ("test", "test"), ("prod", "secure")])
def test_legacy_profile_mapping(env, profile, monkeypatch):
    monkeypatch.delenv("AILAB_PROFILE", raising=False)
    assert Settings(_env_file=None, env=env).profile == profile
    assert Settings(_env_file=None, env=env, profile="test").profile == "test"


def test_redacted_settings_hides_url_and_secrets():
    value = Settings(
        _env_file=None,
        database_url="postgresql://user:p%40ss@db/name?password=secret",
        admin_password="private",
        s3_secret_key="key",
        log_dir="/private/ailab-test-logs",
    )
    output = value.redacted()
    assert output["admin_password"] == output["s3_secret_key"] == "<redacted>"
    assert output["database_url"] == "postgresql://<redacted>@db/name"
    assert output["log_dir"] == "/private/ailab-test-logs"


def test_secure_disables_legacy_and_credentials_are_not_interchangeable(identities, monkeypatch):
    from ailab_platform.service_auth import authenticator, require_scope

    monkeypatch.setattr(settings, "profile", "secure")
    assert authenticator.authenticate(settings.service_token) is None
    assert authenticator.authenticate(identities["agent"]["X-Service-Token"]).service == "agent"
    with pytest.raises(Exception) as error:
        require_scope("files:patch_processing")(identities["agent"]["X-Service-Token"])
    assert error.value.status_code == 403


def test_factories_do_not_fall_back(monkeypatch):
    from ailab_platform.sandbox import get_provider
    from ailab_platform.storage import get_storage

    monkeypatch.setattr(settings, "sandbox_provider", "typo")
    monkeypatch.setattr(settings, "storage_backend", "typo")
    get_provider.cache_clear()
    get_storage.cache_clear()
    try:
        with pytest.raises(ValueError, match="unknown"):
            get_provider()
        with pytest.raises(ValueError, match="unknown"):
            get_storage()
    finally:
        get_provider.cache_clear()
        get_storage.cache_clear()


def test_bound_conversation_and_artifact_reads(identities):
    from ailab_platform.api.main import app
    from ailab_platform.db import SessionLocal, init_db
    from ailab_platform.models import Artifact, Conversation, FileObject, Run, RunStatus, User, new_id
    from ailab_platform.storage import get_storage

    init_db()
    with SessionLocal() as db:
        owner = User(email=f"{new_id('usr')}@test")
        db.add(owner)
        db.flush()
        conv = Conversation(user_id=owner.id, title="scope test")
        db.add(conv)
        db.flush()
        files = [
            FileObject(user_id=owner.id, filename=f"a-{new_id('fil')}.txt", storage_key=f"uploads/{new_id('fil')}", status="ready", size=1)
            for _ in range(3)
        ]
        db.add_all(files)
        db.flush()
        old = Run(
            user_id=owner.id,
            conversation_id=conv.id,
            prompt="old",
            model="test-pro",
            status=RunStatus.SUCCEEDED,
            input_file_ids=[files[0].id, files[1].id],
        )
        active = Run(user_id=owner.id, conversation_id=conv.id, prompt="active", model="test-pro", status=RunStatus.RUNNING)
        db.add_all([old, active])
        db.flush()
        active.parent_run_id = old.id
        conv.active_run_id = active.id
        db.add(Artifact(run_id=active.id, file_id=files[2].id))
        db.commit()
        run_id, old_id = active.id, old.id
        ids = [(file.id, file.storage_key) for file in files]
    for _, key in ids:
        get_storage().put_bytes(key, b"x")
    with TestClient(app) as client:
        for file_id, _ in ids:
            response = client.get(f"/internal/files/{file_id}/content?run_id={run_id}", headers=identities["broker"])
            assert response.status_code == 200 and response.content == b"x"
            assert client.get(f"/internal/files/{file_id}?run_id={old_id}", headers=identities["agent"]).status_code == 403


def test_clients_send_only_caller_credential_and_context(identities, monkeypatch):
    from ailab_platform.clients import AsyncBrokerClient, PlatformClient

    monkeypatch.setattr(settings, "service_name", "broker")
    monkeypatch.setattr(settings, "service_credential", identities["broker"]["X-Service-Token"])
    monkeypatch.setattr(settings, "service_agent_token", identities["agent"]["X-Service-Token"])
    with PlatformClient() as client:
        request = client._get_file_bytes("file", run_id="run")
        assert request.kwargs["params"]["run_id"] == "run"
        assert request.kwargs["headers"] == identities["broker"]
    monkeypatch.setattr(settings, "profile", "secure")
    with pytest.raises(RuntimeError, match="only load its own"):
        AsyncBrokerClient(service="agent")


def test_deployments_receive_only_their_own_caller_secret():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    manifests = list(yaml.safe_load_all((root / "platform/deploy/base/apps/services.yaml").read_text()))
    deployments = {doc["metadata"]["name"]: doc for doc in manifests if doc and doc["kind"] == "Deployment"}
    for name in ("api", "agent", "broker", "worker"):
        container = deployments[f"platform-{name}"]["spec"]["template"]["spec"]["containers"][0]
        secrets = {entry["secretRef"]["name"] for entry in container["envFrom"] if "secretRef" in entry}
        assert {secret for secret in secrets if secret.endswith("-identity")} == {f"platform-{name}-identity"}
        assert "platform-secrets" not in secrets
        assert "platform-service-verifiers" in secrets
        assert "--no-proxy-headers" in container["command"]
        environment = {item["name"]: item for item in container["env"]}
        assert environment["AILAB_SERVICE_NAME"]["value"] == name


def test_secure_open_registration_exposes_invite_mode(monkeypatch):
    from ailab_platform.api.routes_auth import registration_mode

    monkeypatch.setattr(settings, "profile", "secure")
    monkeypatch.setattr(settings, "registration", "open")
    monkeypatch.setattr(settings, "invite_code", "configured-invite")
    assert registration_mode() == "invite"


def test_route_guard_catches_removed_scope_on_included_endpoint():
    from fastapi import Depends

    from ailab_platform.service_auth import require_scope

    app, router = FastAPI(), APIRouter(prefix="/internal")

    @router.get("/scoped", dependencies=[Depends(require_scope("runs:read"))])
    def endpoint():
        return {}

    app.include_router(router)
    assert_internal_scopes(app)
    router.routes[0].dependant.dependencies.clear()
    router.routes[0].dependencies.clear()
    # Include a fresh view after mutating the declared route, as an omitted dependency would do.
    other = FastAPI()
    other.include_router(router)
    with pytest.raises(AssertionError, match="scoped"):
        assert_internal_scopes(other)


def test_secure_profile_rejects_wrong_service_identity():
    with pytest.raises(RuntimeError, match="running service"):
        secure_settings().validate_secrets("broker")


def test_a_sandbox_lease_may_be_shorter_than_a_run_because_the_agent_renews_it():
    from ailab_platform.config import Settings

    # The Agent heartbeats its lease (runtime_heartbeat_seconds), so the TTL no longer has to outlive the run.
    Settings(_env_file=None, sandbox_ttl_seconds=600, run_timeout_seconds=900)
