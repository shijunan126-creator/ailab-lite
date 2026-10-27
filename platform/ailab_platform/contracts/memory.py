"""Bounded user-owned long-term memory, shared by Portal, Agent and the nightly dream."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CHARS = 12000
MAX_ENTRIES = 100
MAX_TOMBSTONES = 200
DREAM_MAX_CHANGES = 5
DREAM_TARGET_RATIO = 0.8
DREAM_WINDOW_START = "02:00"
DREAM_WINDOW_END = "05:00"


def valid_clock(value: str) -> str:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except (ValueError, AttributeError) as exc:
        raise ValueError("time must be HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("time must be HH:MM")
    return f"{hour:02d}:{minute:02d}"


class MemoryEntryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=40)
    kind: Literal["user", "memory"] = "memory"
    content: str = Field(min_length=1, max_length=1000)
    # Optional metadata added by Phase 2. Omitting a field means "keep what is stored"; this
    # keeps older Portal/MemoryUpdate clients from wiping confidence and evidence.
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence_run_ids: list[str] | None = Field(default=None, max_length=50)
    evidence_episode_ids: list[str] | None = Field(default=None, max_length=50)
    entities: list[str] | None = Field(default=None, max_length=20)
    source_quote: str | None = Field(default=None, max_length=1000)
    valid_from: datetime | None = None
    invalid_at: datetime | None = None
    superseded_by: str | None = Field(default=None, max_length=40)
    last_used_at: datetime | None = None
    # "Pause": kept and visible to the user, never shown to the model. Not the same as deleting.
    suppressed: bool | None = None
    project_id: str | None = Field(default=None, max_length=40)
    status: Literal["active", "review"] | None = None

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("memory must contain text without NUL")
        return value.strip()

    @field_validator("evidence_run_ids")
    @classmethod
    def valid_evidence(cls, value):
        if value is None:
            return None
        if any(not rid.startswith("run_") or len(rid) > 40 for rid in value):
            raise ValueError("evidence must be run ids")
        return list(dict.fromkeys(value))


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    enabled: bool = True
    auto_save: bool = True
    reference_history: bool | None = None
    project_only: bool | None = None
    entries: list[MemoryEntryInput] = Field(max_length=MAX_ENTRIES)

    @model_validator(mode="after")
    def bounded(self):
        ids = [e.id for e in self.entries if e.id]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate memory entry id")
        if sum(len(e.content) for e in self.entries) > MAX_CHARS:
            raise ValueError("memory exceeds character budget")
        return self


class MemoryOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["list", "search", "add", "replace", "remove"]
    query: str = Field(default="", max_length=300)
    as_of: datetime | None = None
    expected_revision: int | None = Field(default=None, ge=0)
    id: str | None = Field(default=None, max_length=40)
    kind: Literal["user", "memory"] = "memory"
    content: str = Field(default="", max_length=1000)
    explicit: bool = False

    @model_validator(mode="after")
    def valid_write(self):
        if self.action in {"add", "replace"} and (not self.content.strip() or "\x00" in self.content):
            raise ValueError("memory content is required without NUL")
        return self


class RecallRequest(BaseModel):
    """Search the user's own past conversations."""

    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=300)
    run_id: str | None = Field(default=None, max_length=40)
    cursor: str | None = Field(default=None, max_length=40)
    limit: int = Field(default=5, ge=1, le=10)
    days: int | None = Field(default=None, ge=1, le=3650)
    before: datetime | None = None
    after: datetime | None = None

    @field_validator("before", "after")
    @classmethod
    def utc_range(cls, value):
        return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value

    @field_validator("query")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("query must contain text without NUL")
        return value.strip()

    @model_validator(mode="after")
    def valid_request(self):
        if not self.query and not self.run_id:
            raise ValueError("query or run_id is required")
        if self.before and self.after and self.before <= self.after:
            raise ValueError("before must follow after")
        return self


class MemoryDreamSettings(BaseModel):
    """User-facing nightly consolidation switch; stored beside the memory document."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    timezone: str | None = Field(default=None, max_length=64)
    window_start: str = DREAM_WINDOW_START
    window_end: str = DREAM_WINDOW_END

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be an IANA name") from exc
        return value

    @field_validator("window_start", "window_end")
    @classmethod
    def clock(cls, value):
        return valid_clock(value)

    @model_validator(mode="after")
    def ordered_window(self):
        if self.window_start >= self.window_end:
            raise ValueError("dream window must start before it ends")
        return self


class DreamChange(BaseModel):
    """One validated change proposed by the dream model. The server, not the model, applies it.

    Facts are append-only: add a new fact and, when justified, invalidate an old one. We never
    ask an LLM to overwrite a fact's text in place.
    """

    model_config = ConfigDict(extra="forbid")
    op: Literal["add", "invalidate"]
    id: str | None = Field(default=None, max_length=40)
    kind: Literal["user", "memory"] = "memory"
    content: str = Field(default="", max_length=1000)
    confidence: float = Field(default=0.0, ge=0, le=1)
    evidence_run_ids: list[str] = Field(default_factory=list, max_length=20)
    evidence_episode_ids: list[str] = Field(default_factory=list, max_length=20)
    entities: list[str] = Field(default_factory=list, max_length=20)
    source_quote: str = Field(default="", max_length=1000)
    reason: str = Field(default="", max_length=300)
    project_id: str | None = Field(default=None, max_length=40)
    supersedes: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid(self):
        if self.op == "invalidate" and not self.id:
            raise ValueError("invalidate needs an id")
        if self.op == "add" and (not self.content.strip() or "\x00" in self.content):
            raise ValueError("change content is required")
        return self


class DreamPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: list[DreamChange] = Field(default_factory=list, max_length=MAX_ENTRIES + DREAM_MAX_CHANGES)
    journal: str = Field(default="", max_length=2000)


class MemoryDreamRunOut(BaseModel):
    id: str
    status: str
    trigger: str
    local_date: str
    timezone: str
    cursor_run_id: str | None = None
    input_run_ids: list[str] = Field(default_factory=list)
    changes: list[DreamChange] = Field(default_factory=list)
    journal: str = ""
    token_usage: dict = Field(default_factory=dict)
    error: str = ""
    created_at: datetime | None = None
    finished_at: datetime | None = None
