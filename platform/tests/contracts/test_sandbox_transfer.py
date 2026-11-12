"""The same bounded transfer contract runs on mock, Docker and real OpenSandbox."""

import hashlib
import io
import shlex
import tarfile
import uuid

import pytest

from ailab_platform.config import settings
from ailab_platform.sandbox import SandboxError, SandboxFileNotFound
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
        # A missing file is its own error: the Broker turns it into 404 and the model is told ENOENT,
        # not "unreadable" (which used to make it rewrite an existing file).
        with pytest.raises(SandboxFileNotFound):
            provider.file_size(ref, "/workspace/outputs/missing.bin")
        with pytest.raises(SandboxFileNotFound):
            provider.get_file_to_path(ref, "/workspace/outputs/missing.bin", target, max_bytes=size)
        with pytest.raises(SandboxError) as not_a_file:
            provider.file_size(ref, "/workspace/outputs")
        assert not isinstance(not_a_file.value, SandboxFileNotFound)
        provider.exec(ref, "ln -s /etc/passwd /workspace/outputs/link", 5)
        with pytest.raises(SandboxError):
            provider.get_file_to_path(ref, "/workspace/outputs/link", target, max_bytes=size)
        with pytest.raises(SandboxError):
            provider.put_file_from_path(ref, "/workspace/outputs/link", source, max_bytes=size)
    finally:
        provider.release(ref)


def test_writes_never_pass_through_a_link_and_replace_in_place(provider, tmp_path):  # noqa: F811
    """OpenSandbox writes what its file API proves safe without an exec; everything else meets the shell guard."""
    if provider.name == "mock":
        pytest.skip("the links below would point at the host's /tmp")
    source = tmp_path / "source"
    source.write_bytes(b"first")
    spec = SandboxSpec(image=settings.sandbox_image, ttl=120)
    ref = provider.acquire(f"links-{uuid.uuid4().hex[:12]}", spec.image, spec=spec)
    try:
        setup = "mkdir -p /tmp/outside && ln -s /tmp/outside /workspace/escape && ln -s /tmp/outside/f /workspace/link"
        assert provider.exec(ref, setup, 10).exit_code == 0
        for path in ("/workspace/escape/x.txt", "/workspace/link"):  # a linked parent, a linked file
            with pytest.raises(SandboxError):
                provider.put_file(ref, path, b"x")
            with pytest.raises(SandboxError):
                provider.put_file_from_path(ref, path, source, max_bytes=5)
        assert provider.exec(ref, "ls -A /tmp/outside", 10).stdout.strip() == ""
        provider.put_file_from_path(ref, "/workspace/outputs/r.txt", source, max_bytes=5)
        source.write_bytes(b"again")
        provider.put_file_from_path(ref, "/workspace/outputs/r.txt", source, max_bytes=5)  # replaces, no temporary left
        assert provider.exec(ref, "cat /workspace/outputs/r.txt; echo; ls -A /workspace/outputs", 10).stdout.split() == ["again", "r.txt"]
        provider.remove_file(ref, "/workspace/outputs/r.txt")
        provider.remove_file(ref, "/workspace/outputs/r.txt")  # already gone: not an error
        with pytest.raises(SandboxFileNotFound):
            provider.file_size(ref, "/workspace/outputs/r.txt")
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


def test_deep_output_manifest_and_frozen_revision(provider, tmp_path):  # noqa: F811
    import json

    from ailab_platform.sandbox.file_listing import LIST_PROGRAM, parse_listing

    spec = SandboxSpec(image=settings.sandbox_image, ttl=120)
    ref = provider.acquire(f"identity-{uuid.uuid4().hex[:12]}", spec.image, spec=spec)
    path = "/workspace/outputs/a/b/c/result\t中\n.txt"
    command = " ".join(shlex.quote(p) for p in ["python", "-c", LIST_PROGRAM, "/workspace/outputs", "hash", "500", "100"])
    try:
        provider.put_file(ref, path, b"AA")
        if provider.name != "mock":
            assert provider.list_files(ref, "/workspace/outputs") == [{"path": path, "size": 2}]
        first = parse_listing(provider.exec(ref, command, 60).stdout)
        assert first == [{"path": path, "size": 2, "sha256": hashlib.sha256(b"AA").hexdigest()}]
        provider.put_file(ref, path, b"BB")
        second = parse_listing(provider.exec(ref, command, 60).stdout)
        assert second[0]["sha256"] == hashlib.sha256(b"BB").hexdigest()
        # A missing file is its own error: the Broker turns it into 404 and the model is told ENOENT,
        # not "unreadable" (which used to make it rewrite an existing file).
        with pytest.raises(SandboxFileNotFound):
            provider.file_size(ref, "/workspace/outputs/missing.bin")
        with pytest.raises(SandboxError) as not_a_file:
            provider.file_size(ref, "/workspace/outputs")
        assert not isinstance(not_a_file.value, SandboxFileNotFound)
        provider.exec(ref, "ln -s /etc/passwd /workspace/outputs/link", 5)
        assert not json.loads(provider.exec(ref, command, 60).stdout)["complete"]
    finally:
        provider.release(ref)
