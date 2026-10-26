"""Per-user settings for the nightly memory dream."""

from __future__ import annotations

from ..contracts.memory import MemoryDreamSettings


def dream_settings(user) -> MemoryDreamSettings:
    stored = (user.settings or {}).get("memory_dream") or {}
    try:
        return MemoryDreamSettings(**stored)
    except ValueError:
        # A hand-edited profile must not break every background tick. Fall back to safe defaults;
        # the user can still turn the feature off through the API.
        return MemoryDreamSettings()


def save_dream_settings(db, user, settings: MemoryDreamSettings) -> MemoryDreamSettings:
    user.settings = {**(user.settings or {}), "memory_dream": settings.model_dump(mode="json")}
    db.add(user)
    return settings


def local_window(settings: MemoryDreamSettings, timezone: str) -> tuple[int, int]:
    start_h, start_m = (int(part) for part in settings.window_start.split(":"))
    end_h, end_m = (int(part) for part in settings.window_end.split(":"))
    return start_h * 60 + start_m, end_h * 60 + end_m
