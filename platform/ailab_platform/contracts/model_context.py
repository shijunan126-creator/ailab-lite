"""Internal summary compare-and-set contract, independent of public conversation details."""

import hashlib
import json

from pydantic import BaseModel, Field

CONTEXT_ABI_VERSION = 6


class SummaryUpdate(BaseModel):
    revalidation_ids: list[str] = Field(default_factory=list, max_length=16)
    source_epoch: int = Field(default=0, ge=0)
    expected_through_run_id: str | None = None
    through_run_id: str
    summary: str = Field(min_length=1, max_length=16000)


def tool_catalog_fingerprint(tools: list[dict]) -> str:
    """Diagnostic identity of a frozen ordered catalog; never an authorization check."""
    return hashlib.sha256(json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
