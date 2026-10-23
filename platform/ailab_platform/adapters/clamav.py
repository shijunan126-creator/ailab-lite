"""clamd INSTREAM: one bounded chunk at a time, with an overall deadline."""

import socket
import struct
import time
from typing import BinaryIO
from urllib.parse import urlsplit

from ..ports.scanner import ScanResult


class ClamAVScanner:
    def __init__(self, address: str, timeout: float = 30):
        self.address, self.timeout = address, timeout

    def scan(self, stream: BinaryIO) -> ScanResult:
        deadline = time.monotonic() + self.timeout
        sock = None
        try:
            address = urlsplit(self.address)
            if address.scheme == "unix":
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                endpoint = address.path
            elif address.scheme == "tcp" and address.hostname and address.port:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                endpoint = (address.hostname, address.port)
            else:
                raise ValueError("clamd_address must be tcp://host:port or unix:///path")

            def remaining():
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("scan deadline exceeded")
                sock.settimeout(left)

            remaining()
            sock.connect(endpoint)
            sock.sendall(b"zINSTREAM\0")
            while chunk := stream.read(64 * 1024):
                remaining()
                sock.sendall(struct.pack("!I", len(chunk)))
                remaining()
                sock.sendall(chunk)
            remaining()
            sock.sendall(b"\0\0\0\0")
            reply = bytearray()
            while b"\0" not in reply and len(reply) < 4096:
                remaining()
                data = sock.recv(min(1024, 4096 - len(reply)))
                if not data:
                    break
                reply.extend(data)
            answer = bytes(reply).split(b"\0", 1)[0].decode("utf-8", "replace")
            if b"\0" not in reply:
                return ScanResult("unavailable", "clamav")
            if answer == "stream: OK":
                return ScanResult("clean", "clamav")
            if answer.startswith("stream: ") and answer.endswith(" FOUND"):
                return ScanResult("infected", "clamav", answer[8:-6][:200])
            return ScanResult("unavailable", "clamav")
        except (OSError, ValueError):
            return ScanResult("unavailable", "clamav")
        finally:
            if sock is not None:
                sock.close()
