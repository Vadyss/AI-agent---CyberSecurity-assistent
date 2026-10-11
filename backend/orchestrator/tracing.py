# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Optional OpenTelemetry tracing, active only when OTEL_EXPORTER_OTLP_ENDPOINT is set.

Spans: every HTTP request, one ``toolsmith.run`` span per run with its
pipeline events as span events, and outbound HTTP (model provider, sandbox,
identity provider) as child spans. Headers and bodies are not recorded.
"""
from __future__ import annotations

import os

from opentelemetry import trace

_NOOP = trace.NoOpTracer()


def setup_tracing(app, settings, version: str, *, provider=None) -> object | None:
    """Instrument the app; returns the tracer provider, or None when tracing is off."""
    if provider is None:
        if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
            return None
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({
            "service.name": "toolsmith-backend", "service.version": version,
            "deployment.environment": settings.environment}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls="health,ready,metrics")
    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    return provider


def shutdown_tracing(provider) -> None:
    if provider is None:
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().uninstrument()
    shutdown = getattr(provider, "shutdown", None)
    if shutdown:
        shutdown()


def tracer_for(provider):
    return provider.get_tracer("toolsmith") if provider is not None else _NOOP


def add_event(name: str, attributes: dict) -> None:
    """Records a pipeline event on the current span (no-op without tracing)."""
    span = trace.get_current_span()
    if span.is_recording():
        span.add_event(name, attributes)
