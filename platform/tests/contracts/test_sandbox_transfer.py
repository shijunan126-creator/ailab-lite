"""The same bounded transfer contract runs on mock, Docker and real OpenSandbox."""

import hashlib
import io
import shlex
import tarfile
import uuid

import pytest

from ailab_platform.config import settings
from ailab_platform.sandbox import SandboxError
from ailab_platform.sandbox.base import SandboxSpec
from tests.contracts.test_sandbox_lifecycle import provider  # noqa: F401


def test_streaming_roundtrip_limits_and_symlinks(provider, tmp_path):  # noqa: F811
    source, target = tmp_path / "source", tmp_path / "target"
    with source.open("wb") as stream:
        for _ in range(32):
            stream.write(bytes(range(256)) * 256)
    size = source.stat().st_size
    path = "/workspace/outputs/a '中文.bin"
    spec = SandboxSpec(image=settings.sandbox_image, ttl=120)
    ref = provider.acquire(f"transfer-{uuid.uuid4().hex[:12]}", spec.image, spec=spec)
    try:
        provider.put_file_from_path(ref, path, source, max_bytes=size)
        provider.get_file_to_path(ref, path, target, max_bytes=size)
        with source.open("rb") as a, target.open("rb") as b:
            assert hashlib.file_digest(a, "sha256").hexdigest() == hashlib.file_digest(b, "sha256").hexdigest()
        with pytest.raises(SandboxError):
            provider.get_file_to_path(ref, path, target, max_bytes=size - 1)
        with pytest.raises(SandboxError):
            provider.put_file_from_path(ref, path, source, max_bytes=size - 1)
        provider.exec(ref, "ln -s /etc/passwd /workspace/outputs/link", 5)
        with pytest.raises(SandboxError):
            provider.get_file_to_path(ref, "/workspace/outputs/link", target, max_bytes=size)
        with pytest.raises(SandboxError):
            provider.put_file_from_path(ref, "/workspace/outputs/link", source, max_bytes=size)
    finally:
        provider.release(ref)


def test_injected_nested_parents_remain_writable_by_exec_user(provider, tmp_path):  # noqa: F811
    source = tmp_path / "archive.tar"
    with tarfile.open(source, "w") as archive:
        entry = tarfile.TarInfo("extracted.txt")
        entry.size = 7
        archive.addfile(entry, io.BytesIO(b"payload"))
    root = "/workspace/new '中文/stream/deep"
    spec = SandboxSpec(image=settings.sandbox_image, ttl=120)
    ref = provider.acquire(f"parents-{uuid.uuid4().hex[:12]}", spec.image, spec=spec)
    try:
        provider.put_file_from_path(ref, root + "/source.tar", source, max_bytes=source.stat().st_size)
        provider.put_file(ref, root + "/small/nested/input.txt", b"hello")
        script = (
            "import os,pathlib,tarfile; "
            f"p=pathlib.Path({root!r}); "
            "assert p.stat().st_uid == os.geteuid(); "
            "assert (p/'small/nested').stat().st_uid == os.geteuid(); "
            "(p/'written.txt').write_text('ok'); "
            "tarfile.open(p/'source.tar').extractall(p, filter='data'); "
            "assert (p/'extracted.txt').read_bytes() == b'payload'"
        )
        result = provider.exec(ref, "python -c " + shlex.quote(script), 15)
        assert result.exit_code == 0, result.stderr
        if provider.name != "mock":
            assert provider.exec(ref, "test $(id -u) -ne 0", 5).exit_code == 0
    finally:
        provider.release(ref)
