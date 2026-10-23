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

from pydantic import Field, field_validator, model_validator
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

    # Models: which ones exist, how each is reached and which key it uses is the catalog's business
    # (models.toml, see catalog.py). Nothing here names a model or holds a key.
    models_file: Path | None = None  # default: platform/models.toml (the image sets /etc/ailab/models.toml)
    llm_timeout_seconds: int = Field(default=120, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)  # extra attempts on 429 / 5xx / network errors
    llm_retry_backoff_seconds: float = Field(default=1.0, gt=0)  # doubled each retry
    # Send the model's reasoning_content back in assistant turns. Whether that is
    # required or rejected depends on the DeepSeek model/version, so it is opt-in.
    llm_echo_reasoning: bool = False

    # Sandbox control plane. mock | docker | opensandbox
    sandbox_provider: Literal["mock", "docker", "opensandbox"] = "mock"
    sandbox_image: str = "harbor.ailab.local/sandbox/sbx-office:lite"
    sandbox_ttl_seconds: int = Field(default=4 * 3600, gt=0)
    max_sandboxes: int = Field(default=1, gt=0)
    sandbox_exec_timeout_seconds: int = Field(default=120, gt=0)
    max_artifact_bytes: int = Field(default=100 * 1024 * 1024, gt=0)  # one delivered file
    max_sandbox_read_bytes: int = Field(default=10 * 1024 * 1024, gt=0)  # read_file tool
    max_sandbox_write_bytes: int = Field(default=20 * 1024 * 1024, gt=0)  # write_file tool
    max_auto_deliver_files: int = Field(default=20, gt=0)
    max_workspace_bytes: int = Field(default=60 * 1024 * 1024, gt=0)  # compressed workspace carried between turns of a conversation
    workspace_ttl_days: int = Field(default=3, ge=0)  # keep the last workspace of a conversation at most this long; 0 = keep it
    workspace_cleanup_interval_seconds: int = Field(default=3600, gt=0)  # scan the object store for expired snapshots hourly

    # OpenSandbox server (T2 dev stand-in). Set AILAB_SANDBOX_PROVIDER=opensandbox.
    osb_domain: str = "127.0.0.1:8090"
    osb_protocol: Literal["http", "https"] = "http"
    osb_api_key: str = "dev-osb-key"
    osb_request_timeout_seconds: int = Field(default=600, gt=0)

    # Picture search for slides, done outside the sandbox: openverse | mock | none (see images.py)
    image_search_provider: Literal["openverse", "mock", "none"] = "openverse"
    openverse_token: str = ""
    image_search_timeout_seconds: int = Field(default=20, gt=0)

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
    cached_token_weight: float = Field(default=0.1, ge=0, le=1)
    daily_token_limit: int = Field(default=2_000_000, ge=0)  # per user, prompt + completion, UTC day
    monthly_token_limit: int = Field(default=30_000_000, ge=0)
    library_quota_bytes: int = Field(default=20 * 1024**3, ge=0)  # per user, rejected files excluded

    # Queues
    queue_backend: Literal["redis", "memory"] = "redis"  # redis | memory
    # Streams, not the old list keys (a list key would raise WRONGTYPE).
    run_queue: str = "ailab:stream:runs"
    file_queue: str = "ailab:stream:files"
    run_max_attempts: int = Field(default=3, gt=0)  # deliveries before a run is failed as a poison message
    file_max_attempts: int = Field(default=3, gt=0)
    reaper_interval_seconds: int = Field(default=60, gt=0)
    reaper_grace_seconds: int = Field(default=120, ge=0)  # don't touch work younger than this

    # The Portal proxies the API same-origin, so browsers need no CORS access. Leave empty unless a
    # separate browser app calls the API directly (then also give it its own login, not a shared token).
    cors_origins: str = ""

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
