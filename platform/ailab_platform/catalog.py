"""The model catalog: which models users can choose, and how each one is reached.

Users select a catalog ``id``. Everything about the upstream (URL, upstream model name, the key)
lives here on the server and is never sent to a browser or accepted from a request.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .config import settings
from .thinking import TAG_RE

log = logging.getLogger("ailab.catalog")

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
KEY_ENV_PREFIX = "AILAB_KEY_"  # so a typo cannot make the catalog send e.g. AILAB_SERVICE_TOKEN upstream
PROVIDERS = {"openai", "mock"}
# A reasoning level may add provider fields, never reshape the request the transport builds.
RESERVED_REQUEST_FIELDS = {
    "model", "messages", "tools", "tool_choice", "stream", "stream_options", "max_tokens", "max_completion_tokens", "n",
}  # fmt: skip
REASONING_FILE = "reasoning.toml"


class CatalogError(ValueError):
    pass


def secret(name: str) -> str:
    """A model key: the process environment first, then the .env file used on dev machines."""
    value = os.environ.get(name, "")
    if value:
        return value
    try:
        from dotenv import dotenv_values

        return (dotenv_values(settings.env_file_path()).get(name) or "").strip()
    except Exception:
        return ""


@dataclass(frozen=True)
class ReasoningLevel:
    id: str
    name: str
    body: dict


@dataclass(frozen=True)
class ReasoningProfile:
    """The thinking strengths one model family accepts (reasoning.toml)."""

    name: str
    default: str
    levels: tuple[ReasoningLevel, ...]

    def level(self, level_id: str | None) -> ReasoningLevel | None:
        return next((x for x in self.levels if x.id == level_id), None)

    def resolve(self, level_id: str | None) -> ReasoningLevel:
        """The requested level, else the default: a run's level may not exist on its fallback model."""
        return self.level(level_id) or self.level(self.default)  # type: ignore[return-value]

    def public(self) -> dict:
        return {"default": self.default, "levels": [{"id": x.id, "name": x.name} for x in self.levels]}


@dataclass(frozen=True)
class ModelSpec:
    id: str
    name: str
    description: str = ""
    provider: str = "openai"
    base_url: str = ""
    api_key_env: str = ""
    upstream_model: str = ""
    weight: float = 1.0
    tools: bool = True
    fallback: list[str] = field(default_factory=list)
    context_tokens: int = 32768
    max_output_tokens: int = 8192
    capabilities: list[str] | None = None
    default: bool = False
    enabled: bool = True
    # Thinking models (DeepSeek V4.x) refuse the next turn of a tool conversation unless the previous
    # reasoning_content is sent back; others reject or ignore it. Hence a per-model switch.
    echo_reasoning: bool = False
    # DeepSeek's explicit non-thinking mode for metadata/summary calls. Opt in only
    # for endpoints that support thinking.type; never infer capability from a URL.
    disable_reasoning_for_quiet: bool = False
    # Upstreams that route and cache by conversation (OpenCode Go: x-opencode-session) get a stable,
    # hashed per-conversation id in this header. Empty means no such header.
    session_header: str = ""
    # Name of a reasoning.toml profile: the thinking strengths users can pick for this model.
    reasoning: str = ""
    # For upstreams that send thinking inside the answer text (a server without a reasoning parser): the tag name, e.g.
    # "think" for <think>...</think>. Empty: thinking arrives only in its own field. Never guessed (see thinking.py).
    think_tag: str = ""
    # The chat template opens the thinking block in the prompt (DeepSeek-R1 style): only the closing tag arrives.
    think_prefilled: bool = False

    def supports(self, required: set[str]) -> bool:
        declared = set(self.capabilities) if self.capabilities is not None else ({"tools"} if self.tools else set())
        return required <= declared

    def api_key(self) -> str:
        return secret(self.api_key_env) if self.api_key_env else ""

    @property
    def available(self) -> bool:
        if not self.enabled:
            return False
        if self.provider == "mock":
            return True
        return bool(self.base_url) and (not self.api_key_env or bool(self.api_key()))

    def public(self) -> dict:
        """What a user may see. Deliberately without base_url, upstream_model and anything key-related."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "weight": self.weight,
            "tools": self.supports({"tools"}),
            "capabilities": sorted(self.capabilities if self.capabilities is not None else (["tools"] if self.tools else [])),
        }


class Catalog:
    def __init__(self, models: list[ModelSpec], reasoning: dict[str, ReasoningProfile] | None = None):
        self.models = {m.id: m for m in models}
        self.reasoning = reasoning or {}

    def reasoning_for(self, spec: ModelSpec | None) -> ReasoningProfile | None:
        return self.reasoning.get(spec.reasoning) if spec and spec.reasoning else None

    def get(self, model_id: str | None) -> ModelSpec | None:
        return self.models.get(model_id or "")

    def available(self) -> list[ModelSpec]:
        return [m for m in self.models.values() if m.available]

    def default_id(self) -> str | None:
        """The preselected model: the one marked default if usable, else the first usable one."""
        usable = self.available()
        for m in usable:
            if m.default:
                return m.id
        return usable[0].id if usable else None

    def weight_of(self, model_id: str) -> float:
        """Quota multiplier. Unknown ids (usage recorded before a model was renamed) count as 1."""
        spec = self.models.get(model_id)
        return spec.weight if spec else 1.0


def parse_reasoning(data: dict) -> dict[str, ReasoningProfile]:
    profiles: dict[str, ReasoningProfile] = {}
    for name, raw in data.items():
        where = f"reasoning profile {name!r}"
        if not ID_RE.match(name) or not isinstance(raw, dict):
            raise CatalogError(f"{where}: bad name or not a table")
        if set(raw) - {"default", "levels"}:
            raise CatalogError(f"{where}: unknown keys {sorted(set(raw) - {'default', 'levels'})}")
        items = raw.get("levels")
        if not isinstance(items, list) or not 1 <= len(items) <= 8:
            raise CatalogError(f"{where}: levels must list 1-8 levels")
        levels: list[ReasoningLevel] = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {"id", "name", "body"}:
                raise CatalogError(f"{where}: each level needs exactly id, name and body")
            lid, label, body = item["id"], item["name"], item["body"]
            if not isinstance(lid, str) or not ID_RE.match(lid) or any(x.id == lid for x in levels):
                raise CatalogError(f"{where}: bad or duplicate level id {lid!r}")
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 12:
                raise CatalogError(f"{where}: level {lid!r} needs a name of 1-12 characters")
            if not isinstance(body, dict) or not body or set(body) & RESERVED_REQUEST_FIELDS:
                raise CatalogError(f"{where}: level {lid!r} body must be a non-empty table without request framing fields")
            levels.append(ReasoningLevel(lid, label.strip(), body))
        default = raw.get("default")
        if not any(x.id == default for x in levels):
            raise CatalogError(f"{where}: default {default!r} is not one of its levels")
        profiles[name] = ReasoningProfile(name, default, tuple(levels))
    return profiles


def parse_catalog(data: dict, reasoning: dict[str, ReasoningProfile] | None = None) -> Catalog:
    raw = data.get("models")
    if not isinstance(raw, list) or not raw:
        raise CatalogError("the catalog needs at least one [[models]] entry")
    specs: list[ModelSpec] = []
    for i, item in enumerate(raw, 1):
        where = f"models[{i}]"
        unknown = set(item) - set(ModelSpec.__dataclass_fields__)
        if unknown:
            raise CatalogError(f"{where}: unknown keys {sorted(unknown)}")
        mid = str(item.get("id", ""))
        if not ID_RE.match(mid):
            raise CatalogError(f"{where}: bad id {mid!r} (letters, digits, '.', '_', '-')")
        if any(s.id == mid for s in specs):
            raise CatalogError(f"{where}: duplicate id {mid!r}")
        spec = ModelSpec(**{**item, "id": mid, "name": str(item.get("name") or mid)})
        if spec.provider not in PROVIDERS:
            raise CatalogError(f"{where} ({mid}): provider must be one of {sorted(PROVIDERS)}")
        if type(spec.disable_reasoning_for_quiet) is not bool:
            raise CatalogError(f"{where}: disable_reasoning_for_quiet must be a boolean")
        if spec.provider == "openai" and not spec.base_url:
            raise CatalogError(f"{where} ({mid}): base_url is required for provider 'openai'")
        if spec.api_key_env and not spec.api_key_env.startswith(KEY_ENV_PREFIX):
            raise CatalogError(f"{where} ({mid}): api_key_env must start with {KEY_ENV_PREFIX}")
        if not spec.weight > 0:
            raise CatalogError(f"{where} ({mid}): weight must be > 0")
        if not isinstance(spec.fallback, list) or any(not isinstance(x, str) for x in spec.fallback):
            raise CatalogError(f"{where}: fallback must be an alias list")
        if spec.capabilities is not None and (
            not isinstance(spec.capabilities, list)
            or any(not isinstance(c, str) or c not in {"tools", "vision", "genui"} for c in spec.capabilities)
        ):
            raise CatalogError(f"{where}: invalid capabilities")
        if (
            type(spec.context_tokens) is not int
            or type(spec.max_output_tokens) is not int
            or not 128 <= spec.context_tokens <= 10_000_000
            or not 1 <= spec.max_output_tokens < spec.context_tokens
        ):
            raise CatalogError(f"{where}: invalid context/output budget")
        if not isinstance(spec.think_tag, str) or (spec.think_tag and not TAG_RE.match(spec.think_tag)):
            raise CatalogError(f"{where}: think_tag must be a tag name such as 'think'")
        if type(spec.think_prefilled) is not bool or (spec.think_prefilled and not spec.think_tag):
            raise CatalogError(f"{where}: think_prefilled must be a boolean and needs think_tag")
        if not isinstance(spec.reasoning, str):
            raise CatalogError(f"{where}: reasoning must be a profile name")
        if spec.reasoning and spec.reasoning not in (reasoning or {}):
            raise CatalogError(f"{where} ({mid}): unknown reasoning profile {spec.reasoning!r} (see {REASONING_FILE})")
        specs.append(spec)
    if sum(1 for s in specs if s.default) > 1:
        raise CatalogError("more than one model is marked default")
    aliases = {s.id for s in specs}
    for spec in specs:
        if set(spec.fallback) - aliases or spec.id in spec.fallback:
            raise CatalogError(f"{spec.id}: unknown or self fallback")
    return Catalog(specs, reasoning)


def reasoning_path(catalog_path: Path | str) -> Path:
    """reasoning.toml beside the catalog (the deployed ConfigMap holds both), else the one shipped in platform/."""
    beside = Path(catalog_path).parent / REASONING_FILE
    return beside if beside.is_file() else Path(__file__).resolve().parent.parent / REASONING_FILE


def load_catalog(path: Path | str) -> Catalog:
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise CatalogError(f"model catalog not found: {path} (set AILAB_MODELS_FILE)") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"{path}: {exc}") from exc
    reasoning: dict[str, ReasoningProfile] = {}
    if any(isinstance(m, dict) and m.get("reasoning") for m in data.get("models") or []):
        source = reasoning_path(path)
        try:
            with open(source, "rb") as fh:
                reasoning = parse_reasoning(tomllib.load(fh))
        except FileNotFoundError as exc:
            raise CatalogError(f"reasoning profiles not found: {source}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise CatalogError(f"{source}: {exc}") from exc
    return parse_catalog(data, reasoning)


@lru_cache
def get_catalog() -> Catalog:
    catalog = load_catalog(settings.models_path)
    for spec in catalog.models.values():
        if not spec.available:
            why = "disabled" if not spec.enabled else f"key {spec.api_key_env} is not set"
            log.warning("model %s is hidden from users: %s", spec.id, why)
    log.info("model catalog: %s (default %s)", [m.id for m in catalog.available()], catalog.default_id())
    return catalog
