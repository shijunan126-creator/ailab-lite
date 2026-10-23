"""Configuration for the AILab lite platform.

All settings use the ``AILAB_`` environment prefix and can be supplied through
``platform/.env``. Defaults are tuned for the single-node dev VM.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import tomllib
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[1]

# Environments where the published default tokens are tolerated.
INSECURE_ENVS = {"dev", "test", "smoke", "k3s"}
DEFAULT_SECRETS = {
    "dev_token": "dev-demo-token",
    "service_token": "dev-service-token",
    "osb_api_key": "dev-osb-key",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AILAB_",
        env_file=os.environ.get("AILAB_ENV_FILE", BASE_DIR / ".env"),  # tests point this at nothing
        env_file_encoding="utf-8",
        extra="ignore",
        allow_inf_nan=False,
    )

    env: str = "dev"
    profile: Literal["dev", "test", "secure"] | None = None
    service_name: Literal["api", "agent", "broker", "worker", "ops"] = "api"
    # Verification digests, never other services' bearer credentials.
    service_credentials: dict[Literal["api", "agent", "broker", "worker", "ops"], str] = Field(default_factory=dict, repr=False)
    service_credential: str = Field(default="", repr=False)
    service_api_token: str = Field(default="", repr=False)
    service_agent_token: str = Field(default="", repr=False)
    service_broker_token: str = Field(default="", repr=False)
    service_worker_token: str = Field(default="", repr=False)
    service_ops_token: str = Field(default="", repr=False)
    scanner_backend: Literal["stub", "clamav"] = "stub"
    allow_external_models: bool = False
    trusted_proxies: list[str] = Field(default_factory=list)
    log_format: Literal["text", "json"] = "text"  # text | json (one object per line, with the request id)
    otel_endpoint: str = ""  # Full OTLP/HTTP traces URL; empty means no SDK/exporter is loaded.
    otel_sample_ratio: float = Field(default=0.05, ge=0, le=1)
    api_base_url: str = "http://127.0.0.1:8080"
    broker_base_url: str = "http://127.0.0.1:8081"
    agent_base_url: str = "http://127.0.0.1:8082"
    worker_base_url: str = "http://127.0.0.1:8083"

    data_dir: Path = BASE_DIR / "var"
    log_dir: Path = BASE_DIR / "var" / "log"

    # Data plane. SQLite in dev; PostgreSQL (CNPG) in T4.
    database_url: str = f"sqlite:///{BASE_DIR / 'var' / 'platform.db'}"
    redis_url: str = "redis://127.0.0.1:6379/0"

    # Object storage. local in dev; SeaweedFS S3 in T4.
    storage_backend: Literal["local", "s3"] = "local"
    storage_root: Path | None = None
    s3_bucket: str = "ailab"
    s3_endpoint: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"

    # Auth. Keycloak OIDC replaces the dev token in the full platform.
    dev_token: str = "dev-demo-token"
    dev_user_email: str = "demo@ailab.local"
    service_token: str = "dev-service-token"
    # Password login. The first account is created from these at startup (if absent); more with
    # `python -m ailab_platform.users add`.
    admin_username: str = ""
    admin_password: str = ""
    session_ttl_seconds: int = Field(default=8 * 60 * 60, gt=0)
    # Sign-up on the login page: open (anyone who reaches the Portal) | invite (needs AILAB_INVITE_CODE) | closed
    registration: Literal["open", "invite", "closed"] = "open"
    invite_code: str = ""
    # per client address. Without a reverse proxy that sets X-Forwarded-For every visitor reaches the API as the
    # Portal's address, so this is a brake on scripted sign-ups, not a per-person limit.
    registrations_per_hour: int = Field(default=30, gt=0)
    login_max_failures: int = Field(default=5, gt=0)  # per username and per client address ...
    login_window_seconds: int = Field(default=300, gt=0)  # ... within this many seconds

    # Identity / OIDC (independent of roles and service credentials).
    auth_backend: Literal["local", "oidc", "both"] = "local"
    oidc_label: str = "统一身份登录"
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_redirect_uri: str = ""  # exact Portal /api/auth/oidc/callback URL registered at the IdP
    oidc_groups_claim: str = "groups"  # dotted claim path
    oidc_role_mapping: dict[str, list[Literal["user", "dept_admin", "platform_admin", "auditor"]]] = Field(default_factory=dict)
    oidc_department_mapping: dict[str, str] = Field(default_factory=dict)  # IdP group -> department id
    oidc_jwks_ttl_seconds: int = Field(default=300, ge=10, le=3600)
    oidc_flow_ttl_seconds: int = Field(default=600, ge=60, le=900)
    oidc_http_timeout_seconds: int = Field(default=15, ge=1, le=60)

    @model_validator(mode="after")
    def validate_identity(self):
        if self.auth_backend in {"oidc", "both"}:
            if not self.oidc_client_id:
                raise ValueError("OIDC requires AILAB_OIDC_CLIENT_ID")
            for name in ("oidc_issuer", "oidc_redirect_uri"):
                value = getattr(self, name)
                url = urlsplit(value)
                local = self.env in INSECURE_ENVS and url.hostname in {"localhost", "127.0.0.1", "::1"}
                if (
                    not url.hostname
                    or url.username
                    or url.password
                    or url.fragment
                    or url.query
                    or (url.scheme != "https" and not (local and url.scheme == "http"))
                ):
                    raise ValueError(f"AILAB_{name.upper()} must be an absolute HTTPS URL (loopback HTTP allowed in dev/test)")
        return self

    # Models: which ones exist, how each is reached and which key it uses is the catalog's business
    # (models.toml, see catalog.py). Nothing here names a model or holds a key.
    models_file: Path | None = None  # default: platform/models.toml (the image sets /etc/ailab/models.toml)
    vision_input_tokens: int = Field(default=4096, ge=85, le=100_000)
    ocr_alias: str = ""
    ocr_max_pages: int = Field(default=20, ge=1, le=40)
    ocr_max_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)
    ocr_page_max_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=4 * 1024 * 1024)
    ocr_min_page_chars: int = Field(default=40, ge=1, le=1000)
    ocr_max_output_tokens: int = Field(default=4096, ge=128, le=8192)
    ocr_deadline_seconds: int = Field(default=1800, gt=0, le=86400)  # a job still pending after this fails explicitly
    llm_timeout_seconds: int = Field(default=120, gt=0)
    ttft_timeout_seconds: float = Field(default=30, gt=0)
    llm_idle_timeout_seconds: float = Field(default=60, gt=0)
    llm_total_timeout_seconds: float | None = Field(default=None, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)  # extra attempts on 429 / 5xx / network errors
    llm_retry_backoff_seconds: float = Field(default=1.0, gt=0)  # doubled each retry
    # Send the model's reasoning_content back in assistant turns. Whether that is
    # required or rejected depends on the DeepSeek model/version, so it is opt-in.
    llm_echo_reasoning: bool = False

    # Sandbox control plane. mock | docker | opensandbox
    sandbox_provider: Literal["mock", "docker", "opensandbox"] = "mock"
    sandbox_image: str = "harbor.ailab.local/sandbox/sbx-office:lite"
    runtime_heartbeat_seconds: float = Field(default=60, gt=0, le=60)
    checkpoint_max_bytes: int = Field(default=4 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    ask_user_timeout_hours: int = Field(default=72, ge=1, le=720)
    sandbox_ttl_seconds: int = Field(default=1200, ge=1)
    max_sandboxes: int = Field(default=2, ge=1)
    # Broker lease ownership and single-machine admission; no idle sandbox reuse.
    broker_owner: str = Field(default="single-node", pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    broker_lock_ttl_seconds: int = Field(default=30, ge=3)
    broker_reconcile_interval_seconds: float = Field(default=10, gt=0)
    broker_release_retry_seconds: float = Field(default=2, gt=0)
    broker_record_retention_seconds: int = Field(default=3600, ge=0)
    sandbox_max_lifetime_seconds: int = Field(default=4 * 3600, ge=1)
    sandbox_cpu: float = Field(default=1, gt=0)
    sandbox_memory_bytes: int = Field(default=1536 * 1024**2, ge=1)
    sandbox_disk_bytes: int = Field(default=4 * 1024**3, ge=1)
    sandbox_total_memory_bytes: int = Field(default=3 * 1024**3, ge=1)
    sandbox_max_per_user: int = Field(default=1, ge=1)
    sandbox_queue_limit: int = Field(default=32, ge=1)
    sandbox_render_reserved_slots: int = Field(default=1, ge=0)
    sandbox_render_memory_bytes: int = Field(default=768 * 1024**2, ge=0)
    sandbox_render_max_ttl_seconds: int = Field(default=120, ge=1)
    sandbox_exec_timeout_seconds: int = Field(default=120, gt=0)
    max_artifact_bytes: int = Field(default=100 * 1024 * 1024, gt=0)  # one delivered file
    max_sandbox_read_bytes: int = Field(default=10 * 1024 * 1024, gt=0)  # read_file tool
    max_sandbox_write_bytes: int = Field(default=20 * 1024 * 1024, gt=0)  # write_file tool
    max_auto_deliver_files: int = Field(default=20, gt=0)
    max_workspace_bytes: int = Field(default=60 * 1024 * 1024, gt=0)  # compressed workspace carried between turns of a conversation
    workspace_ttl_days: int = Field(default=3, ge=0)  # keep the last workspace of a conversation at most this long; 0 = keep it
    workspace_cleanup_interval_seconds: int = Field(default=3600, gt=0)  # scan the object store for expired snapshots hourly

    backup_timeout_seconds: int = Field(default=3600, ge=1)

    # Lifecycle: zero retention disables that collector; each tick has bounded work.
    provisioned_storage_bytes: int = Field(default=0, ge=0)  # 0 = capacity unknown
    gc_batch_size: int = Field(default=100, ge=1, le=1000)
    gc_interval_seconds: int = Field(default=3600, ge=60)
    presign_staging_ttl_seconds: int = Field(default=86400, ge=0)
    orphan_objects_grace_seconds: int = Field(default=86400, ge=0)
    run_events_retention_days: int = Field(default=30, ge=0)
    conversation_purge_days: int = Field(default=30, ge=0)

    # OpenSandbox server (T2 dev stand-in). Set AILAB_SANDBOX_PROVIDER=opensandbox.
    osb_domain: str = "127.0.0.1:8090"
    osb_protocol: Literal["http", "https"] = "http"
    osb_api_key: str = "dev-osb-key"
    osb_request_timeout_seconds: int = Field(default=600, gt=0)

    # Picture search for slides, done outside the sandbox: openverse | mock | none (see images.py)
    image_search_provider: Literal["openverse", "mock", "none"] = "openverse"
    openverse_token: str = ""
    image_search_timeout_seconds: int = Field(default=20, gt=0)

    # Web research, executed by the Agent outside the sandbox (the sandbox itself has no egress).
    web_search_provider: str = "none"  # none | searxng | mock
    searxng_url: str = "http://127.0.0.1:8888"
    web_search_timeout_seconds: int = 15
    web_fetch_timeout_seconds: int = 20
    web_max_bytes: int = 2 * 1024 * 1024
    web_max_chars: int = 20_000
    web_max_results: int = 5

    # Directory holding <skill>/SKILL.md. Unset = the repo checkout (dev); the
    # image sets it to /opt/ailab/skills.
    skills_dir: Path | None = None

    # Agent / run limits
    max_agent_steps: int = Field(default=20, gt=0)
    run_max_tokens: int = Field(default=2_000_000, gt=0)  # prompt + completion tokens per run
    sandbox_wait_seconds: int = Field(default=300, gt=0)  # how long a run queues for a free sandbox
    run_timeout_seconds: int = Field(default=900, gt=0)
    max_active_runs_per_user: int = Field(default=2, gt=0)
    max_prompt_chars: int = Field(default=8000, gt=0)
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, gt=0)
    max_archive_bytes: int = Field(default=1024**3, gt=0)
    max_parse_bytes: int = Field(default=20 * 1024 * 1024, gt=0)  # Worker parser cap; bigger files keep metadata only
    # A prompt token the upstream served from its cache costs this fraction of a normal one (design: x0.1).
    cached_token_weight: float = Field(default=0.1, ge=0, le=1, allow_inf_nan=False)
    quota_timezone: str = "UTC"
    usage_reservation_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    model_max_output_tokens: int = Field(default=8192, ge=1, le=10_000_000)
    daily_token_limit: int = Field(default=2_000_000, ge=0)  # per user, weighted tokens in quota_timezone
    monthly_token_limit: int = Field(default=30_000_000, ge=0)
    library_quota_bytes: int = Field(default=20 * 1024**3, ge=0)  # per user; active rejected files excluded, trash included

    # Worker: scanning and the limited parser process (the scanner is selected above, with the secure checks)
    clamd_address: str = "tcp://platform-clamav:3310"
    scan_timeout_seconds: float = Field(default=30, gt=0, le=120)
    parse_memory_mb: int = Field(default=192, ge=96, le=256)
    parse_cpu_seconds: int = Field(default=45, ge=1, le=90)
    parse_timeout_seconds: float = Field(default=60, gt=0, le=120)
    scan_retry_backoff_seconds: float = Field(default=5, gt=0, le=60)

    max_file_versions: int = Field(default=10, ge=1, le=100)
    trash_days: int = Field(default=30, ge=1, le=3650)

    # Queues
    queue_backend: Literal["redis", "memory"] = "redis"  # redis | memory
    # Streams, not the old list keys (a list key would raise WRONGTYPE).
    run_queue: str = "ailab:stream:runs"
    file_queue: str = "ailab:stream:files"
    agent_concurrency: int = Field(default=3, ge=2, le=32)
    chat_reserved_slots: int = Field(default=1, ge=1, le=31)
    drain_seconds: float = Field(default=20, gt=0, le=120)
    outbox_max_attempts: int = Field(default=10, ge=1)
    job_deadline_seconds: int = Field(default=86400, ge=60)
    run_max_attempts: int = Field(default=3, gt=0)  # deliveries before a run is failed as a poison message
    file_max_attempts: int = Field(default=3, gt=0)
    reaper_interval_seconds: int = Field(default=60, gt=0)
    reaper_grace_seconds: int = Field(default=120, ge=0)  # don't touch work younger than this

    # The Portal proxies the API same-origin, so browsers need no CORS access. Leave empty unless a
    # separate browser app calls the API directly (then also give it its own login, not a shared token).
    cors_origins: str = ""

    @model_validator(mode="after")
    def agent_slots(self):
        if self.chat_reserved_slots >= self.agent_concurrency:
            raise ValueError("chat_reserved_slots must be less than agent_concurrency")
        return self

    @field_validator("quota_timezone")
    @classmethod
    def valid_quota_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("quota_timezone must be an IANA timezone") from exc
        return value

    @field_validator("otel_endpoint")
    @classmethod
    def valid_otel_endpoint(cls, value: str) -> str:
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("otel_endpoint must be an HTTP(S) URL without credentials, query or fragment")
        return value

    @model_validator(mode="after")
    def validate_broker_limits(self):
        if self.sandbox_ttl_seconds > self.sandbox_max_lifetime_seconds:
            raise ValueError("sandbox ttl exceeds max lifetime")
        if self.sandbox_render_reserved_slots >= self.max_sandboxes:
            raise ValueError("render reservation must leave at least one general slot")
        if self.sandbox_render_memory_bytes >= self.sandbox_total_memory_bytes:
            raise ValueError("render memory reservation must leave general memory")
        return self

    @property
    def storage_path(self) -> Path:
        return self.storage_root or (self.data_dir / "storage")

    @property
    def models_path(self) -> Path:
        # models.local.toml: this machine's own catalog (deploy.sh --model-base-url / --models-file), not in git
        local = BASE_DIR / "models.local.toml"
        return self.models_file or (local if local.is_file() else BASE_DIR / "models.toml")

    def env_file_path(self) -> Path:
        return Path(os.environ.get("AILAB_ENV_FILE", BASE_DIR / ".env"))

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def skills_path(self) -> Path:
        return self.skills_dir or (BASE_DIR.parent / "images" / "sbx-office" / "skills")

    @model_validator(mode="after")
    def resolve_profile(self) -> Settings:
        if self.profile is None:
            self.profile = "test" if self.env == "test" else "dev" if self.env in INSECURE_ENVS else "secure"
        return self

    @field_validator("trusted_proxies")
    @classmethod
    def validate_proxies(cls, values: list[str]) -> list[str]:
        return [str(ipaddress.ip_network(value, strict=False)) for value in values]

    @field_validator("service_credentials")
    @classmethod
    def validate_verifiers(cls, values: dict[str, str]) -> dict[str, str]:
        if any(not re.fullmatch(r"sha256:[0-9a-f]{64}", value) for value in values.values()):
            raise ValueError("service_credentials values must be sha256:<hex digest>")
        if len(set(values.values())) != len(values):
            raise ValueError("service credentials must be distinct")
        return values

    def caller_token(self, service: str) -> str:
        if self.profile == "secure" and service != self.service_name:
            raise RuntimeError("a service may only load its own caller credential")
        if service == self.service_name and self.service_credential:
            return self.service_credential
        token = getattr(self, f"service_{service}_token", "")
        if token:
            return token
        if self.profile != "secure":
            return self.service_token
        raise RuntimeError(f"missing credential for service {service}")

    def redacted(self) -> dict:
        """Safe even when a URL carries credentials or a query-string secret."""
        values = self.model_dump(mode="json")
        for name, value in values.items():
            if any(part in name for part in ("token", "secret", "password", "key", "credential", "invite_code")):
                values[name] = "<redacted>"
            elif isinstance(value, str) and ("://" in value or name == "osb_domain"):
                values[name] = re.sub(r"//.*@", "//<redacted>@", value).split("?", 1)[0].split("#", 1)[0]
        return values

    def validate_secrets(self, service: str | None = None) -> None:
        """Central startup gate for unsafe defaults; all service lifespans call it."""
        if self.profile != "secure":
            if self.service_token:
                logging.getLogger("ailab.config").warning("legacy shared service token enabled in %s profile", self.profile)
            return
        problems = []
        if service is not None and self.service_name != service:
            problems.append(f"service_name must match the running service ({service})")
        for name, default in DEFAULT_SECRETS.items():
            if getattr(self, name) == default:
                problems.append(f"AILAB_{name.upper()} is still the default")
        if self.sandbox_provider == "mock":
            problems.append("mock sandbox is forbidden")
        if self.scanner_backend == "stub":
            problems.append("stub scanner is forbidden")
        if self.service_token:
            problems.append("legacy AILAB_SERVICE_TOKEN must be empty (shared tokens are forbidden, regardless of length)")
        if self.service_name == "broker" and self.sandbox_provider == "opensandbox" and not self.osb_api_key:
            problems.append("OpenSandbox requires a nonempty API key")
        if self.service_name == "api" and self.registration in {"open", "invite"} and not self.invite_code.strip():
            problems.append("registration requires an invite code")
        try:
            token = self.caller_token(self.service_name)
        except RuntimeError:
            token = ""
        digest = "sha256:" + hashlib.sha256(token.encode()).hexdigest()
        if len(token) < 24 or self.service_credentials.get(self.service_name) != digest:
            problems.append("own service credential must be at least 24 characters and match its verifier")
        if not {"api", "agent", "broker", "worker", "ops"} <= self.service_credentials.keys():
            problems.append("service_credentials must declare all five service identities")
        if not self.allow_external_models:
            with self.models_path.open("rb") as fh:
                models = tomllib.load(fh).get("models", [])
            for model in models:
                if model.get("provider", "openai") == "mock":
                    continue
                host = urlsplit(model.get("base_url", "")).hostname or ""
                try:
                    address = ipaddress.ip_address(host)
                    internal = (
                        address.is_loopback
                        or address in ipaddress.ip_network("10.0.0.0/8")
                        or address in ipaddress.ip_network("172.16.0.0/12")
                        or address in ipaddress.ip_network("192.168.0.0/16")
                        or address in ipaddress.ip_network("fc00::/7")
                    )
                except ValueError:
                    # No DNS at startup: only explicit local service names are considered internal.
                    internal = host == "localhost" or host.endswith((".svc", ".svc.cluster.local", ".ailab.internal"))
                if not internal:
                    problems.append(f"model {model.get('id', '?')} uses an external host; set allow_external_models=true to authorize")
        if problems:
            raise RuntimeError(f"insecure configuration for AILAB_PROFILE={self.profile!r}: " + "; ".join(problems))

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.log_dir, self.storage_path):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
