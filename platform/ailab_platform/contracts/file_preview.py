"""Library preview classification and public manifest, shared without ORM/HTTP imports."""

import re
from pathlib import PurePath
from typing import Literal

from pydantic import BaseModel, Field, model_validator

PAGES = {".pdf", ".docx", ".doc", ".pptx", ".potx", ".ppt", ".odt", ".odp", ".rtf"}
SANDBOX_SHEETS = {".xls", ".ods"}
LOCAL_SHEETS = {".xlsx", ".xlsm", ".csv", ".tsv"}
IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".ico", ".avif", ".svg"}
LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".sh": "bash",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".html": "html",
    ".xml": "xml",
    ".css": "css",
    ".sql": "sql",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".toml": "toml",
    ".ini": "ini",
}
TEXT = {".txt", ".md", ".markdown", ".log", *LANGUAGES}
PREVIEW_VERSION = "library-preview-v1"


class FilePreview(BaseModel):
    status: Literal["ready", "pending", "failed", "unsupported"]
    kind: Literal["pages", "sheets", "image", "text"] | None = None
    pages: list[str] | None = Field(default=None, max_length=40)
    page_count: int | None = Field(default=None, ge=0)
    data: str | None = None
    image: str | None = None
    text: str | None = None
    format: Literal["markdown", "code", "plain"] | None = None
    language: str | None = None
    truncated: bool | None = None
    reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def usable_manifest(self):
        if self.status == "ready":
            if self.kind == "pages" and self.pages and all(re.fullmatch(r"p-\d+\.(?:jpg|png)", n) for n in self.pages):
                return self
            if self.kind == "sheets" and self.data == "sheets.json":
                return self
            if self.kind == "image" and self.image in {"display.webp", "original"}:
                return self
            if self.kind == "text" and self.text == "text.txt" and self.format:
                return self
            raise ValueError("ready manifest is missing its assets")
        if self.status == "failed" and not self.reason:
            raise ValueError("failed manifest requires a reason")
        return self


class PreviewAsset(BaseModel):
    key: str = Field(max_length=500)
    content_type: Literal["image/jpeg", "image/png", "application/json", "text/plain; charset=utf-8"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class PreviewCompletion(BaseModel):
    job_id: str = Field(min_length=1, max_length=40)
    preview: FilePreview
    assets: dict[str, PreviewAsset] = Field(default_factory=dict, max_length=41)


class PreviewPagePublication(BaseModel):
    job_id: str = Field(min_length=1, max_length=40)
    name: str = Field(pattern=r"^p-\d+\.(?:jpg|png)$", max_length=30)
    page_count: int = Field(ge=1, le=100000)
    asset: PreviewAsset


def preview_kind(filename: str, content_type: str) -> str | None:
    suffix = PurePath(filename).suffix.lower()
    if suffix in PAGES or content_type.split(";")[0] == "application/pdf":
        return "pages"
    if suffix in LOCAL_SHEETS | SANDBOX_SHEETS:
        return "sheets"
    if suffix in IMAGES or content_type.startswith("image/"):
        return "image"
    if suffix in TEXT or content_type.startswith("text/"):
        return "text"
    return None


def cached_page_preview(file_id: str, meta: dict) -> tuple[dict, dict] | None:
    """OCR selections are partial: reuse only a complete first-40-page preview, with private assets."""
    try:
        preview = FilePreview.model_validate(meta.get("ocr_preview") or {})
        if preview.status != "ready" or preview.kind != "pages" or not preview.page_count:
            return None
        names = preview.pages or []
        if [int(name[2:-4]) for name in names] != list(range(1, min(40, preview.page_count) + 1)):
            return None
        assets = {name: PreviewAsset.model_validate(meta["ocr_preview_assets"][name]).model_dump() for name in names}
        if any(
            a["key"] != f"derived/{file_id}/preview/{name}" or a["content_type"] != ("image/png" if name.endswith(".png") else "image/jpeg")
            for name, a in assets.items()
        ):
            return None
        return preview.model_dump(exclude_none=True), assets
    except (ValueError, KeyError, TypeError):
        return None


def text_manifest(filename: str, meta: dict) -> dict:
    suffix = PurePath(filename).suffix.lower()
    language = LANGUAGES.get(suffix)
    return {
        "status": "ready",
        "kind": "text",
        "text": "text.txt",
        "format": "markdown" if suffix in {".md", ".markdown"} else "code" if language else "plain",
        "language": language,
        "truncated": bool((meta.get("extract") or {}).get("truncated")),
    }
