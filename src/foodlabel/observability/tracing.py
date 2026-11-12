"""OpenTelemetry tracing exported to Jaeger over OTLP/HTTP (D5).

Degrades to a no-op if the OpenTelemetry packages are not installed or OTLP_ENDPOINT is
unset, so unit tests and local runs do not need the tracing stack.
"""
from __future__ import annotations

import contextlib
from typing import Any, Iterator, Optional

from foodlabel.common.config import get_settings

try:  # pragma: no cover - import guard
    from opentelemetry import propagate, trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    _OTEL = True
except Exception:  # noqa: BLE001
    _OTEL = False

_initialised = False


def init_tracing(service_name: str, app: Optional[Any] = None) -> None:
    global _initialised
    endpoint = get_settings().otlp_endpoint
    if not (_OTEL and endpoint) or _initialised:
        return
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint.rstrip("/") + "/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)
    except Exception:  # noqa: BLE001
        pass
    _initialised = True


@contextlib.contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """Start a child span; attributes are stringified. No-op without OpenTelemetry."""
    if not _OTEL:
        yield None
        return
    tracer = trace.get_tracer("foodlabel")
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            s.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
        yield s


def current_trace_id() -> Optional[str]:
    if not _OTEL:
        return None
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx and ctx.is_valid else None


def inject_context() -> dict:
    """Serialise the current trace context so a queued run can continue the same trace."""
    carrier: dict = {}
    if _OTEL:
        propagate.inject(carrier)
    return carrier


def extract_context(carrier: Optional[dict]):
    if _OTEL and carrier:
        return propagate.extract(carrier)
    return None


@contextlib.contextmanager
def continue_trace(name: str, carrier: Optional[dict], **attrs: Any) -> Iterator[Any]:
    """Start a span that is a child of the context stored at submission time."""
    if not _OTEL:
        yield None
        return
    from opentelemetry import context as otel_context

    token = None
    ctx = extract_context(carrier)
    if ctx is not None:
        token = otel_context.attach(ctx)
    try:
        with span(name, **attrs) as s:
            yield s
    finally:
        if token is not None:
            otel_context.detach(token)
