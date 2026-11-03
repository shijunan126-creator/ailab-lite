"""Lossy semantic background with explicit, verifiable source handles."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SummarySource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str | None = Field(default=None, max_length=40)
    file_id: str | None = Field(default=None, max_length=40)
    block_id: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def require_handle(self):
        if not any((self.run_id, self.file_id, self.block_id)):
            raise ValueError("summary source handle required")
        return self


class SummaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=40)
    text: str = Field(max_length=2000)
    sources: list[SummarySource] = Field(default_factory=list, max_length=8)
    status: Literal["unverified", "verified", "conflict"] = "unverified"


class ReadModule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=64)
    resource: str = Field(max_length=512)
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ContextSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    objective: str = Field(default="", max_length=1000)
    constraints: list[SummaryEntry] = Field(default_factory=list, max_length=32)
    decisions: list[SummaryEntry] = Field(default_factory=list, max_length=32)
    facts: list[SummaryEntry] = Field(default_factory=list, max_length=32)
    open_items: list[SummaryEntry] = Field(default_factory=list, max_length=32)
    next_actions: list[str] = Field(default_factory=list, max_length=20)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)

    read_modules: list[ReadModule] = Field(default_factory=list, max_length=512)

    def sources(self):
        return [
            source
            for group in (self.constraints, self.decisions, self.facts, self.open_items)
            for entry in group
            for source in entry.sources
        ]


def canonical_summary(summary, *, modules=True):
    """Field order is the schema declaration order, never the model's key order."""
    value = summary.model_dump(exclude=set() if modules else {"read_modules"})
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validate_summary(text, allowed_sources):
    if len(text.encode("utf-8")) > 16000:
        raise ValueError("summary exceeds byte budget")
    summary = ContextSummary.model_validate_json(text)
    for source in summary.sources():
        for kind, value in source.model_dump(exclude_none=True).items():
            if value not in allowed_sources.get(kind, set()):
                raise ValueError("unknown summary source handle")
    if any(len(value) > 2000 for value in [*summary.next_actions, *summary.uncertainties]):
        raise ValueError("summary entry exceeds budget")
    if len(canonical_summary(summary).encode("utf-8")) > 16000:
        raise ValueError("canonical summary exceeds byte budget")
    return summary


def format_instructions():
    # Keep the instruction small enough for the 10% headroom at the barrier.
    return (
        "只返回JSON对象，不加Markdown围栏："
        '{"schema_version":1,"objective":"", "constraints":[],"decisions":[],"facts":[],"open_items":[],'
        '"next_actions":[],"uncertainties":[]}。'
        "constraints/decisions/facts/open_items的每项为"
        '{"id":"短标识","text":"事实","sources":[{"run_id":"来源"}],"status":"unverified"}；'
        "sources只能使用给定的run_id/file_id/block_id，最多8个。"
        "每组最多32项，text最多2000字；objective最多1000字；"
        "next_actions/uncertainties只能是字符串数组，各最多20项。"
        "总计不超过16000 UTF-8字节，合并重复状态，不摘要技能正文。"
    )
