"""Stable skill references supplied with a message."""

from pydantic import BaseModel, ConfigDict, Field


class SkillSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    file_id: str | None = Field(default=None, max_length=40)
    at: int | None = None
