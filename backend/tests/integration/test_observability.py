# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Structured logs, request IDs, metrics and readiness."""
from __future__ import annotations

import json
import logging
import time

from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from gatekeeper.api import AUDIT_ACTOR
from orchestrator.main import create_app
from orchestrator.observability import REQUEST_ID, JsonFormatter, bind_log, LOG_FIELDS
from tests.fakes import InProcessSandbox, make_settings


def make_client(tmp_path, sandbox=None):
    return TestClient(create_app(settings=make_settings(data_dir=tmp_path), sandbox=sandbox or InProcessSandbox()))


def test_json_log_lines_carry_context_but_never_exception_text():
    tokens = (REQUEST_ID.set("req-12345678"), AUDIT_ACTOR.set("user:abc"), bind_log(tenant_id="t1", run_id="run_x"))
    try:
        try:
            raise ValueError("password=hunter2")
        except ValueError:
            record = logging.getLogger("t").makeRecord("t", logging.ERROR, __file__, 1, "Run failed.", (), None)
            import sys
            record.exc_info = sys.exc_info()
        line = json.loads(JsonFormatter().format(record))
    finally:
        LOG_FIELDS.reset(tokens[2]); AUDIT_ACTOR.reset(tokens[1]); REQUEST_ID.reset(tokens[0])
    assert line["message"] == "Run failed." and line["level"] == "error"
    assert (line["request_id"], line["actor"], line["tenant_id"], line["run_id"]) == ("req-12345678", "user:abc", "t1", "run_x")
    assert line["exception"] == "ValueError" and "hunter2" not in json.dumps(line)


def test_request_ids_and_access_log(tmp_path, caplog):
    with make_client(tmp_path) as client, caplog.at_level(logging.INFO, logger="toolsmith.access"):
        generated = client.get("/v1/runs?secret=do-not-log")
        assert len(generated.headers["X-Request-ID"]) == 32
        echoed = client.get("/v1/runs", headers={"X-Request-ID": "proxy-abc-123456"})
        assert echoed.headers["X-Request-ID"] == "proxy-abc-123456"
        hostile = client.get("/v1/runs", headers={"X-Request-ID": "x\nforged log line"})
        assert hostile.headers["X-Request-ID"] != "x\nforged log line"
    lines = [r for r in caplog.records if r.name == "toolsmith.access"]
    assert lines and all(r.fields["route"] == "/v1/runs" for r in lines)
    # The test client's own httpx logger prints full URLs; server-side loggers must not.
    server_side = [r.getMessage() + json.dumps(getattr(r, "fields", {})) for r in caplog.records if r.name != "httpx"]
    assert all("do-not-log" not in line for line in server_side)


def test_metrics_cover_requests_runs_model_calls_and_sandbox(tmp_path):
    with make_client(tmp_path) as client:
        run_id = client.post("/v1/runs", json={"request": "detect ssh brute force"}).json()["run_id"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if {r["run_id"]: r["status"] for r in client.get("/v1/runs").json()["runs"]}[run_id] != "running":
                break
            time.sleep(0.02)
        client.post(f"/v1/runs/{run_id}/reject", json={"reason": "metrics test"})
        text = generate_latest(client.app.state.metrics.registry).decode()
    assert 'toolsmith_http_requests_total{method="POST",route="/v1/runs",status="202"} 1.0' in text
    assert 'toolsmith_runs_finished_total{reason_code="",status="rejected"} 1.0' in text
    assert 'toolsmith_llm_calls_total{model="mock",status="success",step="planner"}' in text
    assert 'toolsmith_sandbox_executions_total{mode="run",status="ok"}' in text
    assert run_id not in text and "tenant" not in text


def test_readiness_reports_each_dependency(tmp_path):
    class DownSandbox(InProcessSandbox):
        async def health(self):
            return False

    with make_client(tmp_path) as client:
        ready = client.get("/ready")
        assert ready.status_code == 200 and ready.json() == {
            "status": "ready", "checks": {"database": True, "schema": True, "sandbox": True, "identity_provider": True}}
        assert "Deprecation" not in ready.headers
    with make_client(tmp_path / "down", DownSandbox()) as client:
        ready = client.get("/v1/ready")
        assert ready.status_code == 503 and ready.json()["checks"]["sandbox"] is False
