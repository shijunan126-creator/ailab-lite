"""Single-machine resource budget; a future provider may read node allocatable."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Capacity:
    sandboxes: int
    memory: int
    render_slots: int
    render_memory: int


class CapacityProvider(Protocol):
    def capacity(self) -> Capacity: ...


class StaticCapacityProvider:
    def capacity(self) -> Capacity:
        from ..config import settings

        return Capacity(
            settings.max_sandboxes,
            settings.sandbox_total_memory_bytes,
            settings.sandbox_render_reserved_slots,
            settings.sandbox_render_memory_bytes if settings.sandbox_render_reserved_slots else 0,
        )
