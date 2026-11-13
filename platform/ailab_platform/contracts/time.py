"""Timezone names carried by requests, validated once for every DTO that accepts one."""

from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BeforeValidator


def known_zone(value: Any) -> str | None:
    """The browser's zone is a hint: one this server cannot resolve is dropped, never a reason to refuse the message.

    A browser can report a zone newer than the server's tz database; the run then falls back to the account's zone.
    """
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return value


Timezone = Annotated[str | None, BeforeValidator(known_zone)]
