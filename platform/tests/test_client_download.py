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
