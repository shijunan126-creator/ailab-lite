"""Call-level usage protocol. Token amounts in API windows are weighted tokens; ledger amounts are milli-units."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UsageStatus(StrEnum):
    OPEN = "open"
    SETTLED = "settled"
    ESTIMATED = "estimated"
    RELEASED = "released"
    UNRECONCILED = "unreconciled"


class UsageErrorCode(StrEnum):
    QUOTA_EXCEEDED = "quota_exceeded"
    CALL_CONFLICT = "call_conflict"
    RESERVATION_NOT_FOUND = "reservation_not_found"
    RUN_NOT_ACTIVE = "run_not_active"
    MODEL_UNAVAILABLE = "model_unavailable"
    RESERVATION_REQUIRED = "usage_reservation_required"


class ReserveUsageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str = Field(min_length=1, max_length=80)
    run_id: str | None = Field(default=None, min_length=1, max_length=40)
    subject: Literal["run", "file_job"] = "run"
    file_id: str | None = Field(default=None, min_length=1, max_length=40)
    attempt_id: str = Field(min_length=1, max_length=80)
    alias: str = Field(min_length=1, max_length=120)
    mode: Literal["chat", "work", "ocr"]
    max_output_tokens: int = Field(gt=0, le=10_000_000)
    estimated_input_tokens: int = Field(ge=0, le=10_000_000)

    @model_validator(mode="after")
    def valid_subject(self):
        if self.subject == "run":
            if not self.run_id or self.file_id or self.mode == "ocr":
                raise ValueError("run subject requires run_id and chat/work mode")
        elif not self.file_id or self.run_id or self.mode != "ocr":
            raise ValueError("file_job requires file_id, no run_id and ocr mode")
        return self


class SettleUsageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str = Field(min_length=1, max_length=80)
    # The attempt that reserved the call. Settlement is authenticated by this frozen identity, not by live ownership,
    # so a late settlement from an attempt that has since lost the job still reconciles its own call.
    attempt_id: str = Field(min_length=1, max_length=40)
    actual_backend: str = Field(default="", max_length=120)
    input_tokens: int = Field(default=0, ge=0, le=100_000_000)
    cached_tokens: int = Field(default=0, ge=0, le=100_000_000)
    output_tokens: int = Field(default=0, ge=0, le=100_000_000)
    reasoning_tokens: int = Field(default=0, ge=0, le=100_000_000)
    status: Literal["settled", "estimated", "released"]

    @model_validator(mode="after")
    def valid_counts(self):
        if self.cached_tokens > self.input_tokens or self.reasoning_tokens > self.output_tokens:
            raise ValueError("cached/reasoning tokens must be subsets of input/output tokens")
        if self.status == UsageStatus.RELEASED and any((self.input_tokens, self.cached_tokens, self.output_tokens, self.reasoning_tokens)):
            raise ValueError("released calls must have zero usage")
        return self


class ReservationOut(BaseModel):
    call_id: str
    run_id: str | None
    subject: str = "run"
    file_id: str | None = None
    attempt_id: str
    status: UsageStatus
    reserved_amount: int
    weight: str
    cached_token_weight: str
    policy_version: str
    expires_at: datetime


class UsageOut(BaseModel):
    call_id: str
    run_id: str | None
    subject: str = "run"
    file_id: str | None = None
    attempt_id: str
    mode: str
    alias: str
    actual_backend: str
    input_tokens: int
    cached_tokens: int
    output_tokens: int
    reasoning_tokens: int
    weighted_amount: int
    policy_version: str
    status: UsageStatus
    run_tokens: int
