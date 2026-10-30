"""Versioned transport envelope. Job identity is durable and independent of Redis IDs."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class JobEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1, max_length=40)
    schema_version: Literal[1] = 1
    kind: Literal["run", "file", "preview", "thumbnail", "ocr", "summary", "skill_unpack"]
    run_id: str | None = None
    file_id: str | None = None
    draft_id: str | None = None
    mode: Literal["chat", "work"] = "work"
    attempt: int = Field(default=1, ge=1)
    deadline: datetime
    enqueued_at: datetime | None = None
    request_id: str | None = None
    traceparent: str | None = None

    @model_validator(mode="after")
    def resource(self):
        if self.deadline.tzinfo is None:
            raise ValueError("deadline must include timezone")
        if self.enqueued_at is not None and self.enqueued_at.tzinfo is None:
            raise ValueError("enqueued_at must include timezone")
        if self.kind == "run":
            if not self.run_id or self.file_id or self.draft_id:
                raise ValueError("run job requires run_id only")
        elif self.kind == "skill_unpack":
            if not self.draft_id or self.file_id or self.run_id:
                raise ValueError("skill unpack requires draft_id only")
        elif not self.file_id or self.run_id or self.draft_id:
            raise ValueError("file job requires file_id only")
        return self


class RunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    INTERRUPTED = "interrupted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    TERMINAL = {SUCCEEDED, FAILED, CANCELED}
    ACTIVE = {QUEUED, RUNNING}
