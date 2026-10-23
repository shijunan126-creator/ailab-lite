"""Optional tracing exports real OTLP protobuf with bounded, content-free attributes."""

import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ailab_platform import telemetry
from ailab_platform.config import Settings, settings
from ailab_platform.obs import traceparent_var


def test_disabled_tracing_does_not_import_sdk_or_exporter():
    code = """
import sys
from ailab_platform.telemetry import span
with span('disabled') as value:
    assert value is None
assert 'opentelemetry.sdk' not in sys.modules
assert 'opentelemetry.exporter' not in sys.modules
"""
    env = {**os.environ, "AILAB_OTEL_ENDPOINT": ""}
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "endpoint", ["ftp://collector", "http://user:secret@collector/v1/traces", "http://collector/v1/traces?token=secret"]
)
def test_invalid_export_endpoints_fail_configuration(endpoint):
    with pytest.raises(ValueError):
        Settings(_env_file=None, otel_endpoint=endpoint)


def test_zero_regular_sampling_still_exports_error_without_secret(monkeypatch):
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    bodies = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            bodies.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings, "otel_endpoint", f"http://127.0.0.1:{server.server_port}/v1/traces")
    monkeypatch.setattr(settings, "otel_sample_ratio", 0)
    telemetry.provider.cache_clear()
    parent = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-00"
    token = traceparent_var.set(parent)
    try:
        with telemetry.span("successful"):
            pass
        with pytest.raises(ValueError), telemetry.span("failed", operation="fixture"):
            raise ValueError("super-secret-user-content")
        assert telemetry.provider().force_flush(timeout_millis=5000)
        assert traceparent_var.get() == parent
        decoded = [ExportTraceServiceRequest.FromString(body) for body in bodies]
        spans = [span for body in decoded for resource in body.resource_spans for scope in resource.scope_spans for span in scope.spans]
        assert [span.name for span in spans] == ["failed"]
        assert spans[0].trace_id.hex() == parent.split("-")[1]
        assert spans[0].parent_span_id.hex() == parent.split("-")[2]
        assert all(b"super-secret" not in body for body in bodies)
    finally:
        traceparent_var.reset(token)
        telemetry.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
