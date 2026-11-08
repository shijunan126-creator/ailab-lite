"""Bounded image-address registration. The caller's fenced run supplies ownership."""

from pydantic import BaseModel, ConfigDict, Field


class WebImageCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    image: str = Field(max_length=4096)
    thumbnail_url: str = Field(default="", max_length=4096)
    page_url: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=300)
    alt: str = Field(default="", max_length=120)
    width: int | None = Field(default=None, ge=1, le=100000)
    height: int | None = Field(default=None, ge=1, le=100000)


class RegisterWebImages(BaseModel):
    images: list[WebImageCandidate] = Field(max_length=24)
