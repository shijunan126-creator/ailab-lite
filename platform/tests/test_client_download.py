"""Bounded API downloads retain service/job binding and close failed streams."""

import httpx
import pytest

from ailab_platform.clients import ClientError, PlatformClient


class _Stream(httpx.SyncByteStream):
    closed = False
    read_count = 0

    def __iter__(self):
        for _ in range(100):
            self.read_count += 1
            yield b"x" * 65536

    def close(self):
        self.closed = True


@pytest.mark.parametrize("status,exception", [(200, ValueError), (503, ClientError)])
def test_download_bounds_body_and_always_closes(tmp_path, status, exception):
    stream = _Stream()
    requests = []

    def serve(req):
        requests.append(req)
        return httpx.Response(status, stream=stream)

    with PlatformClient(service="broker") as client:
        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(serve))
        with pytest.raises(exception):
            client.download_file("f", tmp_path / "file", max_bytes=65536, job="preview", preview_job="j")
    assert stream.closed and stream.read_count <= 2
    assert dict(requests[0].url.params) == {"job": "preview", "preview_job": "j"}


def _client(serve):
    client = PlatformClient(service="broker")
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(serve))
    return client


def test_a_compressed_transfer_is_refused_instead_of_inflated(tmp_path):
    """A peer must not turn a bounded download into an unbounded decompression."""
    seen = []

    def serve(req):
        seen.append(req.headers.get("accept-encoding"))
        # a raw stream: content= would make httpx decode it while building the fake response
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=httpx.ByteStream(b"\x1f\x8b" + b"\0" * 64))

    with _client(serve) as client, pytest.raises(ClientError) as exc:
        client.download_file("f", tmp_path / "file", max_bytes=1 << 20)
    assert exc.value.status == 502 and seen == ["identity"]


def test_an_error_body_is_read_only_up_to_a_bound(tmp_path):
    stream = _Stream()
    with _client(lambda req: httpx.Response(502, stream=stream)) as client, pytest.raises(ClientError) as exc:
        client.download_file("f", tmp_path / "file", max_bytes=1 << 20)
    assert len(exc.value.body) <= 4096 and stream.read_count == 1 and stream.closed


def test_a_trickling_peer_cannot_postpone_the_deadline(tmp_path, monkeypatch):
    """Each network read is checked against the deadline, not each coalesced 64 KiB block."""
    clock = iter([0.0, *[i * 10.0 for i in range(1, 100)]])
    monkeypatch.setattr("ailab_platform.clients.time.monotonic", lambda: next(clock))

    class Trickle(httpx.SyncByteStream):
        reads = 0

        def __iter__(self):
            for _ in range(1000):
                self.reads += 1
                yield b"x"  # one byte per read: never fills a 64 KiB block

    trickle = Trickle()
    with _client(lambda req: httpx.Response(200, stream=trickle)) as client, pytest.raises(TimeoutError):
        client.download_file("f", tmp_path / "file", max_bytes=1 << 20)
    assert trickle.reads < 20
