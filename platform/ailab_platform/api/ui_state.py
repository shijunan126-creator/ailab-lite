"""Bounded OpenUI state snapshots, independent of agent results."""

import json
import math
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from ..config import settings


def _scalar(value: object) -> bool:
    return (
        value is None
        or type(value) is bool
        or (type(value) in (int, float) and (type(value) is int or math.isfinite(value)))
        or (isinstance(value, str) and len(value) <= 1000)
    )


def _value(value: object) -> bool:
    if _scalar(value):
        return True
    if not isinstance(value, dict) or set(value) != {"value", "componentType"}:
        return False
    kind, field = value["componentType"], value["value"]
    return (kind is None or (isinstance(kind, str) and len(kind) <= 40)) and (
        _scalar(field) or (isinstance(field, list) and len(field) <= 50 and all(_scalar(item) for item in field))
    )


class UIState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    v: Literal[1]
    blocks: dict[str, dict[str, object]]

    @model_validator(mode="before")
    @classmethod
    def bounded_json(cls, value):
        # Reject non-finite input without echoing it in a JSON validation response.
        try:
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError):
            raise HTTPException(422, "invalid UI state JSON") from None
        if len(encoded) > 16384:
            raise HTTPException(422, "UI state exceeds 16384 bytes")
        return value

    @field_validator("v", mode="before")
    @classmethod
    def version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("unsupported UI state version")
        return value

    @field_validator("blocks")
    @classmethod
    def bounded_blocks(cls, blocks):
        allowed = {str(index) for index in range(settings.genui_max_blocks)}
        for index, entries in blocks.items():
            if index not in allowed or len(entries) > 64:
                raise ValueError("invalid UI state block")
            if any(not 1 <= len(key) <= 120 or not _value(value) for key, value in entries.items()):
                raise ValueError("invalid UI state entry")
        return blocks
