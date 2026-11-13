"""Timezone names carried by requests, validated once for every DTO that accepts one."""

from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator


def iana(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("timezone must be an IANA name") from exc
    return value


Timezone = Annotated[str, AfterValidator(iana)]
