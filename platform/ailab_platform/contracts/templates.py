"""Public per-message template selector (never accepts frozen internal grants)."""

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TemplateSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None = Field(default=None, min_length=1, max_length=40)
    file_id: str | None = Field(default=None, min_length=1, max_length=40)

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.id is None) == (self.file_id is None):
            raise ValueError("select exactly one template or file")
        return self
