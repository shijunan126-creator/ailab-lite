"""Pydantic API schemas."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from .contracts.inbox import QueuedMessageOut
from .contracts.templates import TemplateSelection


class UTCModel(BaseModel):
    @field_serializer("*", when_used="json")
    def serialize_utc(self, value):
        # SQLite drops tzinfo on reads. All database timestamps are UTC, so expose that explicitly:
        # browsers otherwise parse a naive ISO string in the visitor's local timezone.
        if isinstance(value, datetime):
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value


class ORMModel(UTCModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORMModel):
    id: str
    email: str
    display_name: str


class ArtifactOut(ORMModel):
    id: str
    file_id: str
    role: str
    filename: str
    content_type: str
    size: int
    sha256: str
    download_url: str = ""


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)  # the length policy is applied when a password is *set*


class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    display_name: str | None = Field(default=None, max_length=120)
    invite_code: str | None = Field(default=None, max_length=200)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)

    @field_validator("prompt")
    @classmethod
    def require_nonblank_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must contain non-whitespace characters")
        return value


class RunCreate(PromptRequest):
    template: TemplateSelection | None = None
    title: str | None = Field(default=None, max_length=200)
    file_ids: list[str] = Field(default_factory=list, max_length=50)  # the Portal's MAX_ATTACHMENTS
    model: str | None = Field(default=None, max_length=120)
    mode: Literal["chat", "work"] = "work"  # chat: answer in words only, no tools, never a sandbox
    skill: str | None = Field(default=None, max_length=40)  # a skill the user picked (implies work)


class RunVersions(BaseModel):
    index: int
    count: int
    ids: list[str]


class RunOut(ORMModel):
    template: dict | None = None
    parent_run_id: str | None = None
    edited_from: str | None = None
    versions: RunVersions | None = None
    retry_of: str | None = None
    superseded_by_run_id: str | None = None
    id: str
    user_id: str
    conversation_id: str | None = None
    title: str
    prompt: str
    status: str
    model: str
    input_file_ids: list[str]
    options: dict = Field(default_factory=dict)
    summary: str
    error: str
    result: dict
    resumable: bool = False
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    work_ms: int | None = None


class SteeredMessage(BaseModel):
    id: str
    prompt: str


class RunDetail(RunOut):
    steered: list[SteeredMessage] = Field(default_factory=list)
    artifacts: list[ArtifactOut] = Field(default_factory=list)
    input_files: list[dict] = Field(default_factory=list)
    usage: dict = Field(default_factory=dict)


class InternalRunDetail(RunDetail):
    budget_window_id: str | None = None
    project_library: list[dict] = Field(default_factory=list)
    """What the Agent and Broker get: the run plus its conversation context (never sent to browsers)."""

    context_settings: dict = Field(default_factory=dict)
    needs_conversation_title: bool = False
    history: list[dict] = Field(default_factory=list)
    workspace_restore_key: str | None = None
    workspace_expired: bool = False  # the conversation's last workspace was dropped by the retention window
    disabled_skills: list[str] = Field(default_factory=list)
    personal_skills: list[dict] = Field(default_factory=list)
    memory: dict = Field(default_factory=dict)
    project_instructions: str = ""
    brand: dict = Field(default_factory=dict)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class ProjectPatch(ProjectIn):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def require_changes(self):
        if not self.model_fields_set or any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("at least one non-null field is required")
        return self


class ProjectOut(UTCModel):
    instructions: str = ""
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    conversations: int
    files: int


class ConversationCreate(BaseModel):
    project_id: str | None = None
    title: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=120)


class ConversationPatch(BaseModel):
    move_files: bool = False
    pinned: bool | None = None
    title: str = Field(default=None, min_length=1, max_length=200)
    project_id: str | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if "title" in self.model_fields_set and (self.title is None or not self.title.strip()):
            raise ValueError("title must not be blank")
        if "pinned" in self.model_fields_set and self.pinned is None:
            raise ValueError("pinned must be a boolean")
        if "move_files" in self.model_fields_set and "project_id" not in self.model_fields_set:
            raise ValueError("move_files requires project_id")
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class MessageCreate(PromptRequest):
    template: TemplateSelection | None = None
    file_ids: list[str] = Field(default_factory=list, max_length=50)  # the Portal's MAX_ATTACHMENTS
    model: str | None = Field(default=None, max_length=120)
    mode: Literal["chat", "work"] = "work"
    skill: str | None = Field(default=None, max_length=40)


class ConversationOut(UTCModel):
    moved_files: int | None = None
    active_run_id: str | None = None
    pinned_at: datetime | None = None
    forked_from: dict | None = None
    id: str
    project_id: str | None
    title: str
    model: str
    created_at: datetime
    updated_at: datetime
    last_status: str | None = None


class ConversationContext(BaseModel):
    used_tokens: int | None = None
    model: str | None = None
    compact_at_tokens: int | None = None


class ConversationActive(BaseModel):
    run_id: str = Field(min_length=1, max_length=40)


class RunEdit(PromptRequest):
    file_ids: list[str] | None = Field(default=None, max_length=50)
    model: str | None = Field(default=None, max_length=120)
    mode: Literal["chat", "work"] | None = None


class ConversationDetail(ConversationOut):
    queue: list[QueuedMessageOut] = Field(default_factory=list)
    queue_paused: bool = False
    context: ConversationContext = Field(default_factory=ConversationContext)
    has_more: bool = False
    turns: list[RunDetail] = Field(default_factory=list)
    previews: list[dict] = Field(default_factory=list)


class EventOut(ORMModel):
    seq: int
    type: str
    payload: dict
    created_at: datetime


class FileOut(ORMModel):
    id: str
    project_id: str | None
    starred: bool
    filename: str
    content_type: str
    size: int
    sha256: str
    status: str
    version: int
    lineage_id: str
    versions: int
    is_head: bool
    deleted_at: datetime | None
    purge_at: datetime | None
    purged: bool
    trashed_from_project_id: str | None = None
    meta: dict
    created_at: datetime
    download_url: str = ""
    thumbnail_url: str | None = None
    preview: dict | None = None


class LibraryFilePatch(BaseModel):
    starred: bool = None
    project_id: str | None = None

    @model_validator(mode="after")
    def require_changes(self):
        if "pinned" in self.model_fields_set and self.pinned is None:
            raise ValueError("pinned must be a boolean")
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self


class FileBatch(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    action: Literal["star", "unstar", "move", "delete", "trash", "restore", "purge"]
    project_id: str | None = None

    @field_validator("ids")
    @classmethod
    def unique_ids(cls, value):
        return list(dict.fromkeys(value))


class FilePatch(BaseModel):
    meta: dict | None = None
    status: str | None = None


class InputFile(BaseModel):
    file_id: str
    filename: str | None = None


class LeaseCreate(BaseModel):
    run_id: str
    image: str | None = None
    ttl_seconds: int | None = None
    inputs: list[InputFile] = Field(default_factory=list)


class LeaseOut(BaseModel):
    lease_id: str
    run_id: str
    provider: str
    sandbox_id: str
    expires_at: datetime
    inputs: list[dict] = Field(default_factory=list)
    restored: bool | None = None  # a conversation turn: whether the previous turn's workspace came back
    restore_error: str | None = None


class ExecIn(BaseModel):
    cmd: str = Field(min_length=1, max_length=20000)
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)


class ExecOut(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int


class CommitIn(BaseModel):
    path: str
    filename: str | None = None
    role: str = "output"
    content_type: str | None = None


class CommitOut(BaseModel):
    artifact_id: str
    file_id: str
    filename: str
    content_type: str
    size: int
    sha256: str
    download_url: str = ""


class RunStateOut(BaseModel):
    steer_pending: bool = False
    job_id: str | None = None
    status: str
    finished_at: datetime | None = None


class RunStatusIn(BaseModel):
    status: str
    summary: str | None = None
    error: str | None = None
    result: dict | None = None


class EventIn(BaseModel):
    type: str = Field(min_length=1, max_length=60)
    payload: dict = Field(default_factory=dict)


class RunTransitionIn(RunStatusIn):
    events: list[EventIn] = Field(default_factory=list, max_length=50)
    attempt_id: str | None = Field(default=None, max_length=40)


class ArtifactIn(BaseModel):
    run_id: str
    staging_key: str
    filename: str
    content_type: str = "application/octet-stream"
    size: int
    sha256: str
    role: str = "output"


class ConversationFork(BaseModel):
    run_id: str = Field(max_length=40)
    mode: Literal["chat", "work"]


class RunRetry(BaseModel):
    model: str | None = Field(default=None, max_length=120)
    mode: Literal["chat", "work"] | None = None


class ContextSettings(BaseModel):
    auto_compact: bool = False
    threshold_ratio: float = Field(default=0.5, ge=0.2, le=0.95)


class ConversationTitle(BaseModel):
    title: str = Field(min_length=1, max_length=4096)


class ConversationSearchItem(UTCModel):
    conversation_id: str
    title: str
    project_id: str | None
    run_id: str | None
    match: Literal["title", "prompt", "answer"]
    snippet: str
    highlight: list[tuple[int, int]]
    updated_at: datetime


class ConversationSearchResult(BaseModel):
    items: list[ConversationSearchItem]
    next_cursor: str | None
