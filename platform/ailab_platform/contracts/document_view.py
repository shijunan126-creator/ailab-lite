"""Bounded, version-bound document viewer wire contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PARSER_REVISION = "office-viewer-a60ba6a7-v1"
RENDER_REVISION = "viewer-render-1"
NATIVE_BYTES = {"docx": 10 * 1024**2, "xlsx": 8 * 1024**2, "pptx": 20 * 1024**2, "pdf": 20 * 1024**2}
NATIVE_UNITS = {"docx": 10000, "xlsx": 100000, "pptx": 200, "pdf": 300}


class DocRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str | None = Field(default=None, min_length=1, max_length=100)
    run_id: str | None = Field(default=None, min_length=1, max_length=100)
    path: str | None = Field(default=None, min_length=1, max_length=1000)
    version: str = Field(min_length=1, max_length=100)
    at: str = Field(min_length=1, max_length=300)
    quote: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def target(self):
        if not ((self.file_id and not self.run_id and not self.path) or (not self.file_id and self.run_id and self.path)):
            raise ValueError("exactly one document target required")
        return self


class Selection(BaseModel):
    at: str = Field(min_length=1, max_length=300)
    text: str = Field(max_length=4000)


class ViewState(BaseModel):
    view_id: str = Field(min_length=1, max_length=160)
    file_id: str | None = Field(default=None, max_length=100)
    run_id: str | None = Field(default=None, max_length=100)
    path: str | None = Field(default=None, max_length=1000)
    version: str = Field(min_length=1, max_length=100)
    at: str | None = Field(default=None, max_length=300)
    selection: Selection | None = None

    @model_validator(mode="after")
    def target(self):
        if not ((self.file_id and not self.run_id and not self.path) or (not self.file_id and self.run_id and self.path)):
            raise ValueError("exactly one document target required")
        return self


class Target(BaseModel):
    at: str = Field(min_length=1, max_length=300)
    quote: str | None = Field(default=None, max_length=4000)


class DocumentView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["open", "reveal", "get_view", "capture"]
    file_id: str | None = Field(default=None, max_length=100)
    path: str | None = Field(default=None, max_length=1000)
    view_id: str | None = Field(default=None, max_length=160)
    version: str | None = Field(default=None, max_length=100)
    targets: list[Target] = Field(default_factory=list, max_length=8)
    note: str | None = Field(default=None, max_length=1000)
    at: str | None = Field(default=None, max_length=300)
    source: Literal["viewport", "render"] = "viewport"
    max_width: int = Field(default=1600, ge=320, le=2400)


def text_block(refs):
    if not refs:
        return ""
    return (
        "\n\n<document_references>\n" + "\n\n".join(f"{r['filename']} ({r['at']})\n{r['quote']}" for r in refs) + "\n</document_references>"
    )
