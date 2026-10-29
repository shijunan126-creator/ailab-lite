"""Lossy semantic background with explicit, verifiable source handles."""

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
    sources: list[SummarySource] = Field(min_length=1, max_length=8)
    status: Literal["unverified", "verified", "conflict"] = "unverified"


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

    def sources(self):
        return [
            source
            for group in (self.constraints, self.decisions, self.facts, self.open_items)
            for entry in group
            for source in entry.sources
        ]


def format_instructions():
    import json

    return (
        "只返回一个符合以下JSON Schema的JSON对象，不加Markdown围栏。"
        "constraints/decisions/facts/open_items为条目对象数组；next_actions和uncertainties只能是字符串数组。"
        "合并重复进度，保留关键约束、更正和未完成事项，不逐条复述无变化的状态。"
        + json.dumps(ContextSummary.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    )
