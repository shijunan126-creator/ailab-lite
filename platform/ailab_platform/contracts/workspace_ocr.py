"""Bounded image-only OCR in the run's current Work sandbox."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Crop(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    rotation: Literal[0, 90, 180, 270] = 0

    @model_validator(mode="after")
    def inside(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("region outside image")
        return self


class WorkspaceOcr(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    region: Crop | None = None
    cursor: str | None = Field(default=None, max_length=2048)
    operation_id: str | None = Field(default=None, pattern="^dop_[a-f0-9]{20}$")

    @model_validator(mode="after")
    def continuation(self):
        if self.cursor and not self.operation_id:
            raise ValueError("cursor requires operation_id")
        return self


class PreparedImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1, max_length=1024)
    source_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    image_sha256: str = Field(pattern="^[a-f0-9]{64}$")
    width: int = Field(ge=1, le=16_000_000)
    height: int = Field(ge=1, le=16_000_000)
    region: Crop | None = None
    actual_region: Crop | None = None
    preprocessing: Literal["workspace-image-v1"] = "workspace-image-v1"
