"""Bounded user-owned long-term memory, shared by Portal and Agent."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_CHARS = 12000
MAX_ENTRIES = 100


class MemoryEntryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, max_length=40)
    kind: Literal["user", "memory"] = "memory"
    content: str = Field(min_length=1, max_length=1000)

    @field_validator("content")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("memory must contain text without NUL")
        return value.strip()


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    enabled: bool = True
    auto_save: bool = True
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
    action: Literal["list", "add", "replace", "remove"]
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
