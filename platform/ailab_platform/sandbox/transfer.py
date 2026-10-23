"""Shared bounded transfer primitives; file size is enforced while copying, not just by stat."""

import io
import time
from collections.abc import Iterable
from typing import BinaryIO

from .base import SandboxError

CHUNK_SIZE = 64 * 1024
TRANSFER_SECONDS = 120


class TransferLimitError(SandboxError):
    pass


class TransferReader(io.BufferedReader):
    """The SDK requires IOBase; enforce a total deadline even for a trickle upload."""

    def __init__(self, raw: io.FileIO, max_bytes: int):
        super().__init__(raw, buffer_size=CHUNK_SIZE)
        self.deadline = time.monotonic() + TRANSFER_SECONDS
        self.max_bytes = max_bytes

    def read(self, size: int = -1) -> bytes:
        if time.monotonic() >= self.deadline:
            raise SandboxError("file transfer deadline exceeded")
        data = super().read(min(size, CHUNK_SIZE) if size >= 0 else CHUNK_SIZE)
        if self.tell() > self.max_bytes:
            raise TransferLimitError("file exceeds transfer limit")
        return data


def copy_chunks(chunks: Iterable[bytes], target: BinaryIO, *, max_bytes: int, deadline: float | None = None) -> int:
    deadline = deadline if deadline is not None else time.monotonic() + TRANSFER_SECONDS
    size = 0
    for chunk in chunks:
        if time.monotonic() >= deadline:
            raise SandboxError("file transfer deadline exceeded")
        size += len(chunk)
        if size > max_bytes:
            raise TransferLimitError(f"file exceeds transfer limit ({max_bytes} bytes)")
        target.write(chunk)
    return size
