"""Optional, bounded OpenTelemetry export. The disabled path imports no OTel packages."""

from contextlib import contextmanager
from functools import lru_cache

from .config import settings


def selected(span, ratio: float) -> bool:
    # Decide by trace ID so ordinary spans of a trace have the same selection.
    # Error spans are retained even when their ordinary trace was not selected.
    return span.status.status_code.name == "ERROR" or span.context.trace_id < int(ratio * (1 << 128))


@lru_cache(maxsize=1)
def provider():
    if not settings.otel_endpoint:
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import SpanLimits, TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
        from opentelemetry.sdk.trace.sampling import ALWAYS_ON
    except ImportError as exc:
        raise RuntimeError("AILAB_OTEL_ENDPOINT requires the optional platform[otel] dependencies") from exc

    class Exporter(SpanExporter):
        def __init__(self):
            self.delegate = OTLPSpanExporter(endpoint=settings.otel_endpoint, timeout=2)

        def export(self, spans):
            batch = [span for span in spans if selected(span, settings.otel_sample_ratio)]
            return self.delegate.export(batch) if batch else SpanExportResult.SUCCESS

        def shutdown(self):
            self.delegate.shutdown()

    value = TracerProvider(
        resource=Resource.create({"service.name": f"ailab-{settings.service_name}"}),
        sampler=ALWAYS_ON,
        span_limits=SpanLimits(max_attributes=16, max_events=2, max_links=0, max_attribute_length=120),
    )
    value.add_span_processor(BatchSpanProcessor(Exporter(), max_queue_size=256, max_export_batch_size=32, schedule_delay_millis=1000))
    return value


@contextmanager
def span(name: str, **attributes):
    if not settings.otel_endpoint:
        yield None
        return
    from opentelemetry import propagate
    from opentelemetry.trace import Status, StatusCode

    from .obs import traceparent_var

    parent = propagate.extract({"traceparent": traceparent_var.get() or ""})
    tracer = provider().get_tracer("ailab")
    with tracer.start_as_current_span(
        name, context=parent, attributes=attributes, record_exception=False, set_status_on_exception=False
    ) as current:
        context = current.get_span_context()
        token = traceparent_var.set(f"00-{context.trace_id:032x}-{context.span_id:016x}-{int(context.trace_flags):02x}")
        try:
            yield current
        except BaseException as exc:
            current.set_status(Status(StatusCode.ERROR))
            # Exception messages can contain request content or credentials.
            current.add_event("exception", {"exception.type": type(exc).__name__})
            raise
        finally:
            traceparent_var.reset(token)


def shutdown() -> None:
    if settings.otel_endpoint and provider.cache_info().currsize:
        provider().shutdown()
        provider.cache_clear()
