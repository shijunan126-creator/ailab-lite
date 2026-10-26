"""Which turns the memory system may use at all."""

from __future__ import annotations


def excluded_from_memory(options: dict | None) -> bool:
    """A temporary chat is neither read from, nor learned from, nor indexed, nor searched later."""
    return (options or {}).get("memory") == "off"
