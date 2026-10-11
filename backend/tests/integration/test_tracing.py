# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""OpenTelemetry spans for requests, runs and their pipeline events."""
from __future__ import annotations

import time

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from orchestrator.main import create_app
from tests.fakes import InProcessSandbox, make_settings


def test_run_span_records_pipeline_events_and_requests_are_traced(tmp_path):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_app(settings=make_settings(data_dir=tmp_path), sandbox=InProcessSandbox(), tracer_provider=provider)
    with TestClient(app) as client:
        run_id = client.post("/v1/runs", json={"request": "detect ssh brute force"}).json()["run_id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if {r["run_id"]: r["status"] for r in client.get("/v1/runs").json()["runs"]}[run_id] != "running":
                break
            time.sleep(0.02)
        client.get("/health")
    spans = exporter.get_finished_spans()
    run_span = next(s for s in spans if s.name == "toolsmith.run")
    assert run_span.attributes["toolsmith.run_id"] == run_id
    assert run_span.attributes["toolsmith.status"] == "awaiting_approval"
    names = [event.name for event in run_span.events]
    assert names[0] == "run_started" and "awaiting_approval" in names
    http = [s for s in spans if s.name.startswith("POST /v1/runs")]
    assert http, [s.name for s in spans]
    assert not any("health" in s.name for s in spans)  # Probes are not traced.
