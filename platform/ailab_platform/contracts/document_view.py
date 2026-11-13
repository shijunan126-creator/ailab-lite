"""Bounded, version-bound document viewer wire contracts."""

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
    # A clicked shape/page/cell has a locator even when it has no selected text.
    quote: str = Field(max_length=4000)

    @model_validator(mode="after")
    def target(self):
        if not ((self.file_id and not self.run_id and not self.path) or (not self.file_id and self.run_id and self.path)):
            raise ValueError("exactly one document target required")
        return self


class Selection(BaseModel):
    at: str = Field(min_length=1, max_length=300)
    text: str = Field(max_length=4000)


class ViewState(BaseModel):
    representation_revision: str | None = Field(default=None, min_length=1, max_length=100)
    sequence: int | None = Field(default=None, ge=1, le=9007199254740991)
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


def text_block(refs):
    if not refs:
        return ""
    return (
        "\n\n<document_references>\n" + "\n\n".join(f"{r['filename']} ({r['at']})\n{r['quote']}" for r in refs) + "\n</document_references>"
    )


def opening_metadata(*, version, filename, size, file_id=None):
    """An authorization/byte admission result, never a full-text or safety assertion."""
    from pathlib import PurePath

    kind = PurePath(filename).suffix.lower().removeprefix(".")
    supported = kind in NATIVE_BYTES
    native = supported and size <= NATIVE_BYTES[kind]
    return {
        "status": "ready" if supported else "unsupported",
        "version": version,
        "file_id": file_id,
        "parser_revision": PARSER_REVISION,
        "kind": kind,
        "title": filename,
        "bytes": size,
        "native": native,
        "units": None,
        "outline": [],
        "structure": "not_prepared",
        "reason": None if native else "native_admission_limit",
    }
