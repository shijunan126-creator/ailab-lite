"""Inbox and queued-message wire contracts; UTC serialization also covers SQLite."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator


class InboxUTC(BaseModel):
    @field_serializer("*", when_used="json")
    def utc(self, value):
        if isinstance(value, datetime):
            return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value


class NotificationOut(InboxUTC):
    id: str
    kind: Literal["run.succeeded", "run.failed", "run.waiting", "schedule.succeeded", "schedule.failed", "share.received"]
    title: str
    conversation_id: str | None
    run_id: str | None
    share_id: str | None
    created_at: datetime
    read_at: datetime | None


class UnreadOut(InboxUTC):
    unread: int
    latest_at: datetime | None


class NotificationsOut(BaseModel):
    items: list[NotificationOut]
    next_cursor: str | None
    unread: int


class NotificationRead(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=200)
    all: bool = False
    conversation_id: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def one_selector(self):
        if sum([self.ids is not None, self.all, self.conversation_id is not None]) != 1:
            raise ValueError("select ids, all, or conversation_id")
        return self


class QueueCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=8000)
    file_ids: list[str] = Field(default_factory=list, max_length=50)
    model: str | None = Field(default=None, max_length=120)
    mode: Literal["chat", "work"] = "work"
    skill: str | None = Field(default=None, max_length=40)
    steer: bool = False

    @field_validator("prompt")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("prompt must contain text")
        return value


class QueuePatch(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=8000)
    steer: bool | None = None

    @model_validator(mode="after")
    def nonblank(self):
        if "prompt" in self.model_fields_set and (self.prompt is None or not self.prompt.strip()):
            raise ValueError("prompt must contain text")
        if "steer" in self.model_fields_set and self.steer is None:
            raise ValueError("steer must be boolean")
        return self


class QueuedMessageOut(InboxUTC):
    id: str
    prompt: str
    file_ids: list[str]
    model: str
    mode: Literal["chat", "work"]
    skill: str | None
    steer: bool
    state: Literal["queued", "steered", "started", "canceled"]
    created_at: datetime


class SteeredEvent(BaseModel):
    queued_id: str
    prompt: str


class SteerClaimOut(BaseModel):
    items: list[QueuedMessageOut]
