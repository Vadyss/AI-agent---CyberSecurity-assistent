# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Optional independent examination uses the unchanged frontend protocol."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator.llm import LlmResult, count_call
from orchestrator.llm_mock import MockLlm
from orchestrator.main import create_app
from orchestrator.planner import make_roles
from tests.e2e.test_scenarios import start
from tests.fakes import InProcessSandbox, make_settings

ROOT = Path(__file__).resolve().parents[2]
SLUG = "custom_ssh_burst"


class CustomClient(MockLlm):
    def __init__(self, settings, generator=None):
        super().__init__(settings)
        self.generator = generator if generator is not None else (ROOT / "examiner/fixtures/ssh_generator.py").read_text()
        self.calls = []

    async def chat(self, role, system, user, **kwargs):
        self.calls.append({"role": role, "user": user, "context": kwargs.get("context")})
        if role in {"planner", "examiner", "rule_author"}:
            count_call(self.settings.llm_max_calls_per_run)
            if role == "planner":
                result = {"intent": "detection_rule", "log_source": "ssh", "attack_type": "custom",
                          "custom_attack": {"slug": SLUG, "description": "An unusual sequence of login attempts within five minutes."},
                          "skills": [{"name": "ssh_parser", "role": "parser", "status": "existing"},
                                     {"name": "count_window", "role": "aggregation", "status": "existing"}]}
            elif role == "examiner":
                result = {"code": self.generator}
            else:
                result = {"recipe": {"name": SLUG, "attack_type": SLUG, "parser": "ssh_parser",
                          "filter": [{"field": "outcome", "op": "eq", "value": "failure"}],
                          "aggregation": {"skill": "count_window", "params": {"group_by": "src_ip", "window_s": 300}},
                          "condition": {"field": "count", "op": "gte", "value": 10}},
                          "explanation": "Detecting repeated failed logins."}
            return LlmResult(json.dumps(result, ensure_ascii=False), None, "mock-independent")
        return await super().chat(role, system, user, **kwargs)


def custom_app(tmp_path, enabled=True, generator=None, client_type=CustomClient):
    settings = replace(make_settings(), llm_provider="mock", mock_delay_ms=0,
                       examiner_enabled=enabled, data_dir=tmp_path / "data")
    llm = client_type(settings, generator)
    return create_app(settings=settings, sandbox=InProcessSandbox(), roles=make_roles(settings, llm)), llm


def test_custom_attack_complete_with_private_data_and_original_events(tmp_path, monkeypatch):
    seeds = iter([0x1234ABCD, 0xDEADBEEF])
    monkeypatch.setattr("gatekeeper.examiner_data.secrets.randbits", lambda _: next(seeds))
    app, llm = custom_app(tmp_path)
    with TestClient(app) as client, client.websocket_connect("/api/ws") as ws:
        run_id, events = start(client, "I want a custom type of unusual SSH sequence.")
        assert [event["type"] for event in events] == ["run_started", "plan_ready", "skill_reused", "skill_reused",
                 "rule_drafted", "rule_evaluated", "validation_done", "llm_usage", "llm_usage", "summary", "awaiting_approval"]
        assert events[-1]["data"]["metrics_validation"]["passed"]
        assert events[-1]["data"]["new_skills"] == []
        assert [ws.receive_json() for _ in events] == events
        candidates = app.state.gatekeeper.registry.candidates_dir / run_id
        artifacts = list((candidates / "data").rglob("*"))
        assert {path.name for path in artifacts if path.is_file()} == {"auth.log", "labels.json"}
        data = app.state.gatekeeper._custom_data[run_id]
        assert data["tuning"].labels["counts"] == data["validation"].labels["counts"] == {SLUG: 6}
        assert app.state.gatekeeper.candidates_info(run_id) == []
        examiner_call = next(call for call in llm.calls if call["role"] == "examiner")
        assert examiner_call["context"] is None
        assert examiner_call["user"].count("<untrusted_data") == 2
        assert [call["role"] for call in llm.calls] == ["planner", "examiner", "rule_author", "summary"]
        rendered = json.dumps(llm.calls, ensure_ascii=False) + app.state.gatekeeper.audit.path.read_text()
        rendered += "".join(path.read_text() for path in artifacts if path.is_file())
        for secret in (str(0x1234ABCD), str(0xDEADBEEF), "1234abcd", "deadbeef"):
            assert secret not in rendered
        assert client.post(f"/api/runs/{run_id}/approve", json={}).json() == {"status": "approved"}
        assert ws.receive_json()["type"] == "rule_approved"
        assert not candidates.exists()
        assert run_id not in app.state.gatekeeper._custom_data
        assert app.state.gatekeeper.approved_rule(SLUG)["attack_type"] == SLUG


def test_invalid_custom_plan_can_be_corrected_before_single_examination(tmp_path):
    class CorrectingClient(CustomClient):
        async def chat(self, role, system, user, **kwargs):
            reply = await super().chat(role, system, user, **kwargs)
            if role == "planner" and kwargs["context"]["attempt"] == 1:
                invalid = json.loads(reply.text)
                invalid["skills"] = invalid["skills"][:1]
                return LlmResult(json.dumps(invalid), None, "mock-independent")
            return reply

    app, llm = custom_app(tmp_path, client_type=CorrectingClient)
    with TestClient(app) as client:
        run_id, events = start(client, "I want a custom SSH attack type.")
        assert events[-1]["type"] == "awaiting_approval", events
        assert [call["role"] for call in llm.calls] == ["planner", "planner", "examiner", "rule_author", "summary"]
        assert events[1]["type"] == "policy_rejected"
        assert events[1]["data"]["target"] == "plan"
        assert events[1]["data"]["violations"][0]["code"] == "PLAN_STRUCTURE"
        assert client.post(f"/api/runs/{run_id}/approve", json={}).status_code == 200


@pytest.mark.parametrize("enabled,generator,examiner_calls", [
    (False, None, 0),
    (True, "import socket\ndef generate(seed):\n    return {}\n", 1),
    (True, "def generate(seed):\n    return {'lines': [], 'instances': []}\n", 1),
])
def test_custom_attack_failure_is_request_rejected_and_discards_data(tmp_path, enabled, generator, examiner_calls):
    app, llm = custom_app(tmp_path, enabled, generator)
    with TestClient(app) as client:
        run_id, events = start(client, "I want a custom attack type.")
        assert [event["type"] for event in events] == ["run_started", "llm_usage", "run_failed"]
        assert events[-1]["data"]["reason_code"] == "REQUEST_REJECTED"
        assert len([call for call in llm.calls if call["role"] == "examiner"]) == examiner_calls
        assert not (app.state.gatekeeper.registry.candidates_dir / run_id).exists()
        assert run_id not in app.state.gatekeeper._custom_data
