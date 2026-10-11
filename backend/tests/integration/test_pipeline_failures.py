# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gatekeeper.types import ParseFailure, SandboxError
from orchestrator.llm import LlmError
from orchestrator.main import create_app
from tests.e2e.test_scenarios import start
from tests.fakes import InProcessSandbox, tenant_data_dir, test_settings


@pytest.mark.parametrize("error,code", [(LlmError("private provider error"), "LLM_ERROR"),
                                      (ValueError("unexpected"), "INTERNAL_ERROR")])
def test_planner_exception_releases_active_slot(tmp_path, error, code):
    class Planner:
        async def propose(self, *args, **kwargs):
            raise error
    settings = replace(test_settings(), llm_provider="mock", mock_delay_ms=0, data_dir=tmp_path)
    roles = SimpleNamespace(planner=Planner())
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox(), roles=roles)) as client:
        run_id, events = start(client, "hello")
        assert events[-1]["type"] == "run_failed"
        assert events[-1]["data"]["reason_code"] == code
        assert events[-1]["phase"] == "plan"
        second, _ = start(client, "second")
        assert second != run_id


def test_timeout_never_leaves_running_state(tmp_path):
    class Planner:
        async def propose(self, *args, **kwargs):
            await asyncio.Event().wait()
    settings = replace(test_settings(), llm_provider="mock", data_dir=tmp_path, run_timeout_s=.04)
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox(), roles=SimpleNamespace(planner=Planner()))) as client:
        run_id, events = start(client, "hello")
        assert events[-1]["data"]["reason_code"] == "INTERNAL_ERROR"
        assert "time limit" in events[-1]["data"]["reason"]


def test_three_invalid_plans_are_bounded_and_reported(tmp_path):
    class Planner:
        async def propose(self, *args, **kwargs):
            return ParseFailure("invalid")
    settings = replace(test_settings(), llm_provider="mock", data_dir=tmp_path)
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox(), roles=SimpleNamespace(planner=Planner()))) as client:
        _, events = start(client, "hello")
        assert [e["type"] for e in events] == ["run_started"] + ["policy_rejected"] * 3 + ["llm_usage", "run_failed"]
        assert events[-1]["data"]["reason_code"] == "PLAN_INVALID"


def test_sandbox_error_is_distinct_from_skill_test_failure(tmp_path):
    class UnavailableSandbox(InProcessSandbox):
        async def health(self):
            return False
        async def test(self, *args, **kwargs):
            raise SandboxError("unavailable")
    settings = replace(test_settings(), llm_provider="mock", mock_delay_ms=0, data_dir=tmp_path)
    with TestClient(create_app(settings=settings, sandbox=UnavailableSandbox())) as client:
        _, events = start(client, "spray")
        assert events[-1]["data"]["reason_code"] == "SANDBOX_ERROR"
        assert events[-1]["phase"] == "forge"


def test_llm_budget_is_per_run_and_counted(tmp_path):
    settings = replace(test_settings(), llm_provider="mock", mock_delay_ms=0, data_dir=tmp_path, llm_max_calls_per_run=1)
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox())) as client:
        run_id, events = start(client, "spray")
        assert events[-1]["data"]["reason_code"] == "LLM_ERROR"
        assert client.app.state.store.lookup(run_id).stats.llm_calls == 1


def test_summary_failure_uses_fallback(tmp_path):
    from orchestrator.planner import make_roles
    settings = replace(test_settings(), llm_provider="mock", mock_delay_ms=0, data_dir=tmp_path)
    roles = make_roles(settings)
    async def broken_summary(*args, **kwargs):
        raise LlmError("provider down")
    roles.summarizer.summarize = broken_summary
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox(), roles=roles)) as client:
        _, events = start(client, "inject")
        assert events[-1]["type"] == "awaiting_approval"
        assert "is awaiting approval" in next(e["data"]["text"] for e in events if e["type"] == "summary")


def test_failed_run_saves_complete_usage_before_terminal_event(tmp_path):
    settings = replace(test_settings(), llm_provider="mock", mock_delay_ms=0, data_dir=tmp_path,
                       llm_max_calls_per_run=1)
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox())) as client:
        run_id, events = start(client, "Detect password spraying on SSH.")
        usage = [e for e in events if e["type"] == "llm_usage"]
        assert [e["data"]["kind"] for e in usage] == ["call", "summary"]
        assert events[-2]["type"] == "llm_usage" and events[-1]["type"] == "run_failed"
        saved = json.loads((tenant_data_dir(settings) / "runs" / run_id / "usage.json").read_text())
        assert len(saved["records"]) == client.app.state.store.lookup(run_id).stats.llm_calls == 1
        assert saved["summary"] == usage[-1]["data"]["summary"]
        assert saved["records"][0]["estimated"] and saved["records"][0]["cost_source"] == "mock"
