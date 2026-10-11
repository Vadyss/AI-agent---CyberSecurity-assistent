# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Prometheus metrics, served on a separate internal port (METRICS_PORT).

Labels never contain tenant IDs, user data or run IDs: that keeps cardinality
bounded and keeps tenant activity out of a shared monitoring system.
"""
from __future__ import annotations

from decimal import Decimal

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server

LATENCY_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)
RUN_BUCKETS = (5, 15, 30, 60, 120, 300, 600, 1200, 1800)


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self.requests = Counter("toolsmith_http_requests_total", "HTTP requests.", ["method", "route", "status"], registry=r)
        self.latency = Histogram("toolsmith_http_request_duration_seconds", "HTTP request latency.",
                                 ["method", "route"], buckets=LATENCY_BUCKETS, registry=r)
        self.runs = Counter("toolsmith_runs_finished_total", "Runs that reached a terminal state.",
                            ["status", "reason_code"], registry=r)
        self.run_seconds = Histogram("toolsmith_run_duration_seconds", "Run duration until its terminal state.",
                                     ["status"], buckets=RUN_BUCKETS, registry=r)
        self.llm_calls = Counter("toolsmith_llm_calls_total", "Model calls.", ["step", "model", "status"], registry=r)
        self.llm_tokens = Counter("toolsmith_llm_tokens_total", "Model tokens (provider-reported or estimated).",
                                  ["step", "model"], registry=r)
        self.llm_cost = Counter("toolsmith_llm_cost_usd_total", "Known model cost in USD.", ["model"], registry=r)
        self.llm_unknown_cost = Counter("toolsmith_llm_unknown_cost_calls_total", "Model calls with unknown cost.",
                                        ["model"], registry=r)
        self.sandbox = Counter("toolsmith_sandbox_executions_total", "Sandbox executions.", ["mode", "status"], registry=r)
        self.sandbox_seconds = Histogram("toolsmith_sandbox_duration_seconds", "Sandbox execution time.", ["mode"],
                                         buckets=LATENCY_BUCKETS, registry=r)
        self.queue_depth = Gauge("toolsmith_queue_depth", "Runs waiting in the queue.", registry=r)
        self.active_runs = Gauge("toolsmith_active_pipelines", "Pipelines currently executing.", registry=r)

    def observe_request(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.labels(method, route, str(status)).inc()
        self.latency.labels(method, route).observe(seconds)

    def run_finished(self, status: str, reason_code: str | None, seconds: float) -> None:
        self.runs.labels(status, reason_code or "").inc()
        self.run_seconds.labels(status).observe(seconds)

    def llm_call(self, record: dict) -> None:
        step, model = str(record.get("step", "unknown")), str(record.get("model", "unknown"))
        self.llm_calls.labels(step, model, str(record.get("status", "unknown"))).inc()
        if isinstance(record.get("total_tokens"), int):
            self.llm_tokens.labels(step, model).inc(record["total_tokens"])
        if record.get("cost_usd") is None:
            self.llm_unknown_cost.labels(model).inc()
        else:
            self.llm_cost.labels(model).inc(float(Decimal(record["cost_usd"])))

    def sandbox_execution(self, mode: str, status: str | None, seconds: float) -> None:
        self.sandbox.labels(mode, status or "unknown").inc()
        self.sandbox_seconds.labels(mode).observe(seconds)

    def serve(self, port: int, host: str = "0.0.0.0") -> None:
        start_http_server(port, addr=host, registry=self.registry)
