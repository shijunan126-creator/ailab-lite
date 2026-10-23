"""Internal summary compare-and-set contract, independent of public conversation details."""

from pydantic import BaseModel, Field


class SummaryUpdate(BaseModel):
    expected_through_run_id: str | None = None
    through_run_id: str
    summary: str = Field(min_length=1, max_length=16000)
