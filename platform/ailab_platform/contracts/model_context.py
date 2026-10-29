"""Internal summary compare-and-set contract, independent of public conversation details."""

from pydantic import BaseModel, Field


class SummaryUpdate(BaseModel):
    revalidation_ids: list[str] = Field(default_factory=list, max_length=16)
    source_epoch: int = Field(default=0, ge=0)
    expected_through_run_id: str | None = None
    through_run_id: str
    summary: str = Field(min_length=1, max_length=16000)
