import io
import zipfile

import pytest

from ailab_platform.adapters.clamav import ClamAVScanner
from ailab_platform.ports.scanner import EicarScanner
from ailab_platform.worker.isolate import IsolationError, execute


def test_stub_scan_verdicts():
    assert EicarScanner().scan(io.BytesIO(b"clean")).verdict == "clean"
    assert EicarScanner().scan(io.BytesIO(b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE")).verdict == "infected"


def test_scanner_down_is_unavailable():
    assert ClamAVScanner("tcp://127.0.0.1:1", timeout=0.1).scan(io.BytesIO(b"clean")).verdict == "unavailable"


def test_bomb_rejected_before_parser(tmp_path):
    src = tmp_path / "bomb.docx"
    with zipfile.ZipFile(src, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * 2_000_000)
    with pytest.raises(IsolationError, match="archive_limit"):
        execute("extract", src, tmp_path / "out", filename=src.name)


@pytest.mark.parametrize("suffix", ["pdf", "docx"])
def test_corrupt_documents_controlled(tmp_path, suffix):
    src = tmp_path / f"bad.{suffix}"
    src.write_bytes(b"broken")
    with pytest.raises(IsolationError, match="corrupt"):
        execute("extract", src, tmp_path / "out", filename=src.name)


@pytest.mark.parametrize("unix", [False, True])
@pytest.mark.parametrize(
    "reply,verdict",
    [
        (b"stream: OK\0", "clean"),
        (b"stream: Eicar-Test-Signature FOUND\0", "infected"),
        (b"INSTREAM size limit exceeded. ERROR\0", "unavailable"),
        (b"stream: OK", "unavailable"),
    ],
)
def test_clamd_stream_protocol(tmp_path, unix, reply, verdict):
    import socket
    import struct
    import threading

    server = socket.socket(socket.AF_UNIX if unix else socket.AF_INET, socket.SOCK_STREAM)
    if unix:
        import uuid

        path = "/tmp/w1-clamd-" + uuid.uuid4().hex + ".sock"
        server.bind(path)
        address = "unix://" + path
    else:
        server.bind(("127.0.0.1", 0))
        address = f"tcp://127.0.0.1:{server.getsockname()[1]}"
    server.listen(1)
    chunks = []
    errors = []

    def receive():
        try:
            with server, server.accept()[0] as conn:
                conn.settimeout(2)

                def read(n):
                    result = b""
                    while len(result) < n:
                        part = conn.recv(n - len(result))
                        assert part
                        result += part
                    return result

                assert read(10) == b"zINSTREAM\0"
                while size := struct.unpack("!I", read(4))[0]:
                    assert size <= 64 * 1024
                    chunks.append(read(size))
                conn.sendall(reply)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=receive)
    thread.start()
    result = ClamAVScanner(address, timeout=2).scan(io.BytesIO(b"x" * 150_000))
    thread.join(3)
    if unix:
        from pathlib import Path

        Path(path).unlink(missing_ok=True)
    assert not thread.is_alive() and not errors
    assert b"".join(chunks) == b"x" * 150_000 and result.verdict == verdict


def test_scan_unavailable_retries_and_remains_quarantined(monkeypatch):
    from ailab_platform.config import settings
    from ailab_platform.ports.scanner import ScanResult
    from ailab_platform.queue import InMemoryQueue
    from ailab_platform.worker import main, pipeline
    from tests.test_worker import ImagePlatform

    calls = []

    class Down:
        def scan(self, stream):
            calls.append(stream.read(10))
            return ScanResult("unavailable", "clamav")

    platform = ImagePlatform(b"hello", filename="hello.txt")
    monkeypatch.setattr(pipeline, "get_scanner", lambda: Down())
    monkeypatch.setattr(main, "process_file", lambda fid: pipeline.process_file(fid, platform=platform))
    monkeypatch.setattr(settings, "scan_retry_backoff_seconds", 0.001)
    q = InMemoryQueue()
    q.enqueue(settings.file_queue, {"file_id": "down"})
    main.handle_message(q, q.dequeue(settings.file_queue))
    assert len(calls) == settings.file_max_attempts
    assert not q.outstanding(settings.file_queue)
    assert platform.file["status"] == "pending"
    assert platform.file["meta"]["processing"] == "scan_unavailable"
    assert platform.file["meta"]["scan_exhausted"] is True
    assert pipeline.process_file("down", platform=platform)["skipped"] == "scan_exhausted"


@pytest.mark.parametrize("kind", ["entries", "expanded"])
def test_archive_entry_and_expansion_limits(tmp_path, kind):
    source = tmp_path / "bad.docx"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_STORED) as archive:
        if kind == "entries":
            for i in range(2049):
                archive.writestr(str(i), b"")
        else:
            archive.writestr("big", b"x" * (128 * 1024 * 1024 + 1))
    with pytest.raises(IsolationError, match="archive_limit"):
        execute("extract", source, tmp_path / "out", filename=source.name)


def test_clamd_deadline(tmp_path):
    import socket
    import threading

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    done = threading.Event()

    def slow():
        with listener, listener.accept()[0]:
            done.wait(1)

    thread = threading.Thread(target=slow)
    thread.start()
    try:
        result = ClamAVScanner(f"tcp://127.0.0.1:{listener.getsockname()[1]}", timeout=0.05).scan(io.BytesIO(b"x"))
        assert result.verdict == "unavailable"
    finally:
        done.set()
        thread.join(2)
