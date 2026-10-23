"""A scan failure never grants permission to consume an uploaded file."""

import os
from dataclasses import dataclass
from typing import BinaryIO, Literal, Protocol


@dataclass(frozen=True)
class ScanResult:
    verdict: Literal["clean", "infected", "unavailable"]
    engine: str
    signature: str | None = None


class Scanner(Protocol):
    def scan(self, stream: BinaryIO) -> ScanResult: ...


class EicarScanner:
    """Development fixture, not an antivirus engine."""

    def scan(self, stream: BinaryIO) -> ScanResult:
        tail = b""
        while chunk := stream.read(64 * 1024):
            data = tail + chunk
            if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
                return ScanResult("infected", "stub", "EICAR test signature")
            tail = data[-64:]
        return ScanResult("clean", "stub")


def get_scanner() -> Scanner:
    from ..config import settings

    if settings.scanner_backend == "clamav":
        from ..adapters.clamav import ClamAVScanner

        return ClamAVScanner(settings.clamd_address, timeout=settings.scan_timeout_seconds)
    if (
        settings.scanner_backend == "stub"
        and settings.env in {"dev", "test", "smoke", "k3s"}
        and getattr(settings, "profile", os.environ.get("AILAB_PROFILE", "dev")) != "secure"
    ):
        return EicarScanner()
    raise ValueError("stub scanner is allowed only in development/test; unknown scanner backend is forbidden")
