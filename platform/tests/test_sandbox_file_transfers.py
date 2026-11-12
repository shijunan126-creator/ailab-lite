"""File transfers through the real Docker and OpenSandbox provider code.

Only the transport is replaced: the provider's own method builds the guarded shell, which runs
for real against a directory standing in for the sandbox, and OpenSandbox's file API is served
from that directory with its measured semantics (lstat info, parents created on write, a move
that refuses to replace). A fake that reimplements the method (as the mock provider must) cannot
catch a provider routing its window through a path its own download refuses, which is how every
``read`` once failed on both real backends.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from ailab_platform.sandbox.base import ExecResult, SandboxError, SandboxFileNotFound
from ailab_platform.sandbox.docker import DockerSandbox
from ailab_platform.sandbox.opensandbox import OpenSandboxProvider

REAL_RUN = subprocess.run  # the Docker transport is patched on the shared subprocess module


@pytest.fixture(autouse=True, scope="module")
def gnu_realpath():
    if subprocess.run(["realpath", "-m", "/x/../y"], capture_output=True).returncode:
        pytest.fail('需要 GNU realpath（macOS: export PATH="$(brew --prefix coreutils)/libexec/gnubin:$PATH"，见 AGENTS.md §9）')


class Sandbox:
    """A directory standing in for the sandbox's /workspace and /tmp."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()  # macOS /var is a link; the script's realpath must land inside
        (self.root / "workspace").mkdir()
        (self.root / "tmp").mkdir()
        (self.root / "outside").mkdir()

    def local(self, path: str) -> Path:
        return Path(self.translate(path))

    def translate(self, text: str) -> str:
        # One pass, and only where a path starts: on Linux the root itself is under /tmp, so a
        # second replace (or a match inside a path) would rewrite the root it just inserted.
        return re.sub(r"(?<![\w./-])(?:/workspace|/tmp/)", lambda match: f"{self.root}{match.group(0)}", text)

    def run(self, script: str, **kwargs) -> subprocess.CompletedProcess:
        return REAL_RUN(["bash", "-c", self.translate(script)], **kwargs)


def docker(sandbox: Sandbox, monkeypatch) -> DockerSandbox:
    provider = DockerSandbox.__new__(DockerSandbox)

    def run(args, **kwargs):
        assert args[:7] == ["docker", "exec", "-u", "10001", "sbx", "sh", "-c"]  # one exec, as the sandbox user
        return sandbox.run(args[7], **kwargs)

    monkeypatch.setattr("ailab_platform.sandbox.docker.subprocess.run", run)
    return provider


class Files:
    """OpenSandbox 1.1.1's file API as measured against a real server."""

    def __init__(self, sandbox: Sandbox) -> None:
        self.sandbox = sandbox

    def get_file_info(self, paths):
        out = {}
        for path in paths:
            mode = os.lstat(self.sandbox.local(path))  # a missing path fails the whole batch, as FILE_NOT_FOUND does
            kind = "symlink" if stat.S_ISLNK(mode.st_mode) else "directory" if stat.S_ISDIR(mode.st_mode) else "file"
            out[path] = SimpleNamespace(entry_type=kind, size=0 if kind == "directory" else mode.st_size)
        return out

    def read_bytes_stream(self, path, chunk_size):
        data = self.sandbox.local(path).read_bytes()  # follows links, like the real stream
        yield from (data[i : i + chunk_size] for i in range(0, len(data), chunk_size))

    def write_file(self, path, data, mode):
        local = self.sandbox.local(path)
        local.parent.mkdir(parents=True, exist_ok=True)  # the real API creates missing parents (as execd's user)
        local.write_bytes(data if isinstance(data, bytes) else data.read())

    def move_files(self, entries):
        for entry in entries:
            if os.path.lexists(self.sandbox.local(entry.dest)):
                raise RuntimeError(f"destination path already exists: {entry.dest}")
            self.sandbox.local(entry.src).rename(self.sandbox.local(entry.dest))

    def delete_files(self, paths):
        for path in paths:
            self.sandbox.local(path).unlink()


def opensandbox(sandbox: Sandbox, monkeypatch) -> OpenSandboxProvider:
    try:
        import opensandbox.models.filesystem  # noqa: F401
    except ImportError:  # the SDK is optional (CI runs without it); the provider only needs its MoveEntry
        for name in ("opensandbox", "opensandbox.models", "opensandbox.models.filesystem"):
            monkeypatch.setitem(sys.modules, name, ModuleType(name))
        sys.modules["opensandbox.models.filesystem"].MoveEntry = lambda src, dest: SimpleNamespace(src=src, dest=dest)
    provider = OpenSandboxProvider.__new__(OpenSandboxProvider)
    provider.execs = []

    def execute(ref, command, timeout):
        provider.execs.append(command)
        done = sandbox.run(command, capture_output=True)
        return ExecResult(done.returncode, done.stdout.decode(), done.stderr.decode(), 1)

    provider.exec = execute
    files = Files(sandbox)
    provider._sandbox = lambda ref: SimpleNamespace(files=files)
    return provider


PROVIDERS = [docker, opensandbox]


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    box = Sandbox(tmp_path)
    box.local("/workspace/big.log").write_bytes(b"".join(b"line %06d\n" % i for i in range(1, 100_001)))
    return box


@pytest.mark.parametrize("make", PROVIDERS)
def test_a_window_starts_at_the_line_and_stops_at_its_size(sandbox, monkeypatch, tmp_path, make):
    provider, target = make(sandbox, monkeypatch), tmp_path / "window"
    provider.get_file_lines_to_path("sbx", "/workspace/big.log", target, line=1, max_bytes=24)
    assert target.read_bytes() == b"line 000001\nline 000002\n"
    provider.get_file_lines_to_path("sbx", "/workspace/big.log", target, line=90_000, max_bytes=30)
    assert target.read_bytes() == b"line 090000\nline 090001\nline 0"
    provider.get_file_lines_to_path("sbx", "/workspace/big.log", target, line=100_002, max_bytes=30)
    assert target.read_bytes() == b""  # past the end: nothing, not an error
    assert not list((sandbox.root / "tmp").iterdir())  # no temporary file outlives the read


@pytest.mark.parametrize("make", PROVIDERS)
def test_a_window_refuses_what_file_size_refuses(sandbox, monkeypatch, tmp_path, make):
    provider, target = make(sandbox, monkeypatch), tmp_path / "window"
    (sandbox.root / "outside" / "secret").write_bytes(b"secret\n")
    sandbox.local("/workspace/escape").symlink_to(sandbox.root / "outside")
    sandbox.local("/workspace/link.txt").symlink_to(sandbox.root / "outside" / "secret")
    sandbox.local("/workspace/dir").mkdir()

    with pytest.raises(SandboxFileNotFound, match="no such file"):
        provider.get_file_lines_to_path("sbx", "/workspace/none.txt", target, line=1, max_bytes=10)
    with pytest.raises(SandboxError, match="not a regular file"):
        provider.get_file_lines_to_path("sbx", "/workspace/dir", target, line=1, max_bytes=10)
    for path in ("/workspace/escape/secret", "/workspace/link.txt"):  # a linked parent, a linked file
        with pytest.raises(SandboxError, match="refusing to read through a link"):
            provider.get_file_lines_to_path("sbx", path, target, line=1, max_bytes=10)
    with pytest.raises(SandboxError, match="must stay under /workspace"):
        provider.get_file_lines_to_path("sbx", "/tmp/x", target, line=1, max_bytes=10)
    assert not list((sandbox.root / "tmp").iterdir())


def links(sandbox: Sandbox) -> None:
    (sandbox.root / "outside" / "secret").write_bytes(b"secret\n")
    sandbox.local("/workspace/escape").symlink_to(sandbox.root / "outside")
    sandbox.local("/workspace/link.txt").symlink_to(sandbox.root / "outside" / "secret")


# An exec costs about a second on OpenSandbox 1.1.1, its file API a few milliseconds (measured):
# what the file API can prove takes no exec, and everything else still meets the guarded shell.


def test_opensandbox_reads_a_plain_file_without_an_exec(sandbox, monkeypatch, tmp_path):
    provider, target = opensandbox(sandbox, monkeypatch), tmp_path / "window"
    provider.get_file_lines_to_path("sbx", "/workspace/big.log", target, line=1, max_bytes=24)
    assert target.read_bytes() == b"line 000001\nline 000002\n"
    assert provider.file_size("sbx", "/workspace/big.log") == 1_200_000
    provider.get_file_to_path("sbx", "/workspace/big.log", target, max_bytes=2_000_000)
    assert target.stat().st_size == 1_200_000 and provider.execs == []
    provider.get_file_lines_to_path("sbx", "/workspace/big.log", target, line=90_000, max_bytes=2_000_000)
    assert target.read_bytes().startswith(b"line 090000\nline 090001\n") and provider.execs == []  # within the window
    provider.get_file_lines_to_path("sbx", "/workspace/big.log", target, line=2, max_bytes=12)
    assert target.read_bytes() == b"line 000002\n" and len(provider.execs) == 1  # larger than the window: one exec
    assert not list((sandbox.root / "tmp").iterdir())


SMALL_FILES = [b"", b"a", b"a\n", b"a\nb", b"a\nb\n", b"\n\n\n", b"x\r\ny\r\n", "第一行\n第二行\n\n末行".encode()]


@pytest.mark.parametrize("content", SMALL_FILES)
def test_opensandbox_cuts_a_window_exactly_where_tail_does(sandbox, monkeypatch, tmp_path, content):
    """A file within the window is cut by the provider instead of ``tail``: the bytes must be the same."""
    sandbox.local("/workspace/small.txt").write_bytes(content)
    shell, provider = docker(sandbox, monkeypatch), opensandbox(sandbox, monkeypatch)
    expected, actual = tmp_path / "expected", tmp_path / "actual"
    for line in range(1, 7):
        for max_bytes in (1, 2, 5, 100):
            execs = len(provider.execs)
            shell.get_file_lines_to_path("sbx", "/workspace/small.txt", expected, line=line, max_bytes=max_bytes)
            provider.get_file_lines_to_path("sbx", "/workspace/small.txt", actual, line=line, max_bytes=max_bytes)
            assert actual.read_bytes() == expected.read_bytes(), (line, max_bytes)
            assert (len(provider.execs) == execs) == (line == 1 or len(content) <= max_bytes), (line, max_bytes)


@pytest.mark.parametrize("content", SMALL_FILES)
def test_lines_are_skipped_across_chunk_boundaries(content):
    from ailab_platform.sandbox.opensandbox import _from_line

    for size in (1, 2, 3, 7):
        for line in range(1, 7):
            chunks = (content[i : i + size] for i in range(0, len(content), size))
            assert b"".join(_from_line(chunks, line)) == b"\n".join(content.split(b"\n")[line - 1 :]), (size, line)


def test_opensandbox_sizes_what_the_file_api_cannot_prove_through_the_shell(sandbox, monkeypatch):
    provider = opensandbox(sandbox, monkeypatch)
    links(sandbox)
    for path in ("/workspace/escape/secret", "/workspace/link.txt"):
        with pytest.raises(SandboxError, match="not a regular file"):
            provider.file_size("sbx", path)
    with pytest.raises(SandboxFileNotFound):
        provider.file_size("sbx", "/workspace/none.txt")
    assert len(provider.execs) == 3


def test_opensandbox_writes_through_real_directories_without_an_exec(sandbox, monkeypatch, tmp_path):
    provider = opensandbox(sandbox, monkeypatch)
    provider.put_file("sbx", "/workspace/a.txt", b"a")
    provider.put_file("sbx", "/workspace/big.log", b"replaced")
    sandbox.local("/workspace/inputs").mkdir()
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    provider.put_file_from_path("sbx", "/workspace/inputs/u.bin", first, max_bytes=100)
    assert provider.execs == []
    provider.put_file_from_path("sbx", "/workspace/inputs/u.bin", second, max_bytes=100)
    assert len(provider.execs) == 1  # the file API's move will not replace a file: one atomic mv
    provider.put_file("sbx", "/workspace/work/new/b.txt", b"b")  # missing parents: made by the sandbox user's mkdir -p
    assert len(provider.execs) == 2 and "mkdir -p" in provider.execs[-1]
    assert sandbox.local("/workspace/a.txt").read_bytes() == b"a"
    assert sandbox.local("/workspace/big.log").read_bytes() == b"replaced"
    assert [p.name for p in sandbox.local("/workspace/inputs").iterdir()] == ["u.bin"]  # no upload temporary left
    assert sandbox.local("/workspace/inputs/u.bin").read_bytes() == b"second"
    provider.remove_file("sbx", "/workspace/inputs/u.bin")
    assert not sandbox.local("/workspace/inputs/u.bin").exists() and len(provider.execs) == 2


def test_opensandbox_refuses_to_write_through_a_link(sandbox, monkeypatch, tmp_path):
    provider = opensandbox(sandbox, monkeypatch)
    links(sandbox)
    source = tmp_path / "source"
    source.write_bytes(b"x")
    for path in ("/workspace/escape/x.txt", "/workspace/link.txt"):
        with pytest.raises(SandboxError, match="refusing to write through a symlink"):
            provider.put_file("sbx", path, b"x")
        with pytest.raises(SandboxError, match="refusing to write through a symlink"):
            provider.put_file_from_path("sbx", path, source, max_bytes=10)
    assert sorted(p.name for p in (sandbox.root / "outside").iterdir()) == ["secret"]
    assert (sandbox.root / "outside" / "secret").read_bytes() == b"secret\n"


def test_only_the_expected_missing_path_lookup_is_kept_out_of_the_log():
    import logging

    from ailab_platform.sandbox.opensandbox import _MISS

    def record(message, error):
        return logging.LogRecord("sdk", logging.ERROR, __file__, 1, message, (), (type(error), error, None))

    assert not _MISS.filter(record("Failed to get file info for 3 paths", RuntimeError("[FILE_NOT_FOUND] lstat /workspace/a")))
    assert _MISS.filter(record("Failed to get file info for 3 paths", RuntimeError("[RUNTIME_ERROR] permission denied")))
    assert _MISS.filter(record("Failed to write file", RuntimeError("[FILE_NOT_FOUND] x")))
