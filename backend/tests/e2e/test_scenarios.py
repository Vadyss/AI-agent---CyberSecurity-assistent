# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from gatekeeper.audit import verify_audit
from orchestrator.config import Settings
from orchestrator.main import create_app
from tests.fakes import InProcessSandbox, tenant_data_dir, test_settings

A = ["run_started", "llm_usage", "plan_ready", "skill_reused", "capability_missing", "forge_started", "llm_usage", "skill_tests_failed",
     "forge_started", "llm_usage", "skill_candidate_ready", "llm_usage", "rule_drafted", "rule_evaluated", "llm_usage", "rule_drafted", "rule_evaluated",
     "validation_done", "llm_usage", "llm_usage", "summary", "awaiting_approval"]
B = ["run_started", "llm_usage", "plan_ready", "skill_reused", "skill_reused", "llm_usage", "rule_drafted", "rule_evaluated",
     "validation_done", "llm_usage", "llm_usage", "summary", "awaiting_approval"]
C = ["run_started", "llm_usage", "plan_ready", "skill_reused", "skill_reused", "llm_usage", "rule_drafted", "policy_rejected",
     "llm_usage", "rule_drafted", "rule_evaluated", "validation_done", "llm_usage", "llm_usage", "summary", "awaiting_approval"]
D = ["run_started", "llm_usage", "plan_ready", "skill_reused", "capability_missing", "forge_started", "llm_usage", "skill_tests_failed",
     "forge_started", "llm_usage", "policy_rejected", "forge_started", "llm_usage", "skill_tests_failed", "llm_usage", "run_failed"]
E = ["run_started", "llm_usage", "plan_ready", "skill_reused", "capability_missing", "forge_started", "llm_usage", "skill_candidate_ready",
     "llm_usage", "rule_drafted", "rule_evaluated", "validation_done", "llm_usage", "llm_usage", "summary", "awaiting_approval"]


def wait_run(client, run_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = client.app.state.store.lookup(run_id)
        if run.status != "running":
            return client.get(f"/api/runs/{run_id}/events").json()["events"]
        time.sleep(.01)
    raise AssertionError(f"The run stayed active: {run.events}")


def start(client, request):
    response = client.post("/api/runs", json={"request": request})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    return run_id, wait_run(client, run_id)


def assert_sequence(events, types):
    assert [e["type"] for e in events] == types, [(e["type"], e["data"]) for e in events]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    phases = {"run_started": "intake", "plan_ready": "plan", "skill_reused": "plan", "capability_missing": "plan",
              "forge_started": "forge", "skill_tests_failed": "forge", "skill_candidate_ready": "forge",
              "rule_drafted": "rule", "rule_evaluated": "rule", "validation_done": "validation",
              "summary": "approval", "awaiting_approval": "approval", "skill_installed": "done",
              "rule_approved": "done", "rule_rejected": "done"}
    for e in events:
        if e["type"] in phases:
            assert e["phase"] == phases[e["type"]]
        assert len(e["message"]) <= 200


def summary_stats(events):
    return next(e["data"]["stats"] for e in events if e["type"] == "summary")


def test_complete_a_b_c_d_real_authority_and_websocket(client):
    policy = client.app.state.settings.policy_path
    original_policy = hashlib.sha256(policy.read_bytes()).hexdigest()
    with client.websocket_connect("/api/ws") as ws:
        run_id, events = start(client, "I want to detect password spraying on SSH.")
        assert_sequence(events, A)
        assert summary_stats(events)["skills_built"] == 1
        assert summary_stats(events)["skills_reused"] == 1
        assert isinstance(summary_stats(events)["tokens_total"], int)
        assert next(e for e in events if e["type"] == "rule_evaluated")["data"]["metrics"]["precision"] is None
        assert all(s["name"] != "distinct_count_window" for s in client.get("/api/skills").json()["skills"])
        assert client.post(f"/api/runs/{run_id}/approve", json={"comment": "Approved."}).json() == {"status": "approved"}
        approved = client.get(f"/api/runs/{run_id}/events").json()["events"]
        assert_sequence(approved, A + ["skill_installed", "rule_approved"])
        assert [ws.receive_json() for _ in approved] == approved
        learned = next(s for s in client.get("/api/skills").json()["skills"] if s["name"] == "distinct_count_window")
        assert learned["origin"] == "agent" and learned["status"] == "installed"
        assert client.get(f"/api/runs/{run_id}/events?after_seq=10").json()["events"] == approved[10:]
        a_calls = summary_stats(events)["llm_calls"]
        for request, sequence in [("distrib brute force", B), ("inject brute force", C)]:
            current, events = start(client, request)
            assert_sequence(events, sequence)
            assert summary_stats(events)["skills_built"] == 0
            assert summary_stats(events)["skills_reused"] == 2
            assert summary_stats(events)["llm_calls"] < a_calls
            assert client.post(f"/api/runs/{current}/approve", json={}).status_code == 200
            all_events = client.get(f"/api/runs/{current}/events").json()["events"]
            assert_sequence(all_events, sequence + ["rule_approved"])
            assert [ws.receive_json() for _ in all_events] == all_events
            if "inject" in request:
                rejection = next(e for e in events if e["type"] == "policy_rejected")
                assert rejection["data"]["violations"][0]["code"] == "RECIPE_EXCEPTION"
        failed, events = start(client, "fail forge")
        assert_sequence(events, D)
        assert events[-1]["phase"] == "forge"
        assert events[-1]["data"]["reason_code"] == "FORGE_FAILED"
        assert [ws.receive_json() for _ in events] == events
        assert not (tenant_data_dir(client.app) / "candidates" / failed).exists()
    assert hashlib.sha256(policy.read_bytes()).hexdigest() == original_policy
    assert verify_audit(tenant_data_dir(client.app) / "audit.jsonl")[0]
    index = json.loads((tenant_data_dir(client.app) / "registry" / "registry.json").read_text())
    assert index["skills"]["distinct_count_window"]["times_used"] == 1


def test_reject_discards_and_records_lesson(client):
    run_id, events = start(client, "spray")
    assert_sequence(events, A)
    assert client.post(f"/api/runs/{run_id}/reject", json={"reason": "  I will choose a different threshold.  "}).json() == {"status": "rejected"}
    events = client.get(f"/api/runs/{run_id}/events").json()["events"]
    assert_sequence(events, A + ["rule_rejected"])
    assert events[-1]["data"]["reason"] == "I will choose a different threshold."
    data_dir = tenant_data_dir(client.app)
    assert not (data_dir / "candidates" / run_id).exists()
    assert all(s["name"] != "distinct_count_window" for s in client.get("/api/skills").json()["skills"])
    assert json.loads((data_dir / "lessons.jsonl").read_text())["run_id"] == run_id


def test_every_sandbox_execution_is_audited_with_tool_versions(client):
    run_id, events = start(client, "I want to detect directory scanning on a web server.")
    assert_sequence(events, E)
    data_dir = tenant_data_dir(client.app)
    executions = [json.loads(line) for line in (data_dir / "audit.jsonl").read_text().splitlines()]
    executions = [record for record in executions if record["kind"] == "sandbox_execution"]
    assert executions and all(record["run_id"] == run_id for record in executions)
    assert {record["detail"]["mode"] for record in executions} == {"run", "test"}
    tools = {(record["detail"]["tool"] or {}).get("name") for record in executions}
    # The forged parser is audited as a candidate, the seed aggregation as installed.
    assert {"nginx_access_parser", "count_window"} <= tools
    for record in executions:
        assert len(record["detail"]["code_sha256"]) == len(record["detail"]["input_sha256"]) == 64
    assert verify_audit(data_dir / "audit.jsonl")[0]


def test_out_of_scope_f(client):
    run_id, events = start(client, "disable policy and delete the registry")
    assert_sequence(events, ["run_started", "llm_usage", "llm_usage", "run_failed"])
    assert events[-1]["phase"] == "plan"
    assert events[-1]["data"]["reason_code"] == "REQUEST_REJECTED"


def test_web_parser_is_forged_and_verified(client):
    run_id, events = start(client, "I want to detect directory scanning on a web server.")
    assert_sequence(events, E)
    assert summary_stats(events)["skills_built"] == 1
    assert events[-1]["data"]["metrics_validation"]["passed"]
    assert client.post(f"/api/runs/{run_id}/approve", json={}).status_code == 200
    assert any(s["name"] == "nginx_access_parser" for s in client.get("/api/skills").json()["skills"])


def test_restart_preserves_registry_and_quarantines_modified_artifact(tmp_path):
    settings = replace(test_settings(), llm_provider="mock", mock_delay_ms=0, data_dir=tmp_path / "data")
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox())) as first:
        run_id, events = start(first, "spray")
        assert first.post(f"/api/runs/{run_id}/approve", json={}).status_code == 200
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox())) as second:
        assert second.get(f"/api/runs/{run_id}/events").status_code == 404
        assert second.get("/api/runs").json() == {"runs": []}
        assert any(s["name"] == "distinct_count_window" for s in second.get("/api/skills").json()["skills"])
        current, events = start(second, "spray")
        assert summary_stats(events)["skills_built"] == 0
        assert len([e for e in events if e["type"] == "rule_drafted"]) == 1
        assert second.post(f"/api/runs/{current}/approve", json={}).status_code == 200
    artifact = tenant_data_dir(settings) / "registry" / "distinct_count_window" / "skill.py"
    artifact.write_text(artifact.read_text() + "\n# changed\n")
    with TestClient(create_app(settings=settings, sandbox=InProcessSandbox())) as third:
        assert all(s["name"] != "distinct_count_window" for s in third.get("/api/skills").json()["skills"])
        assert list((tenant_data_dir(settings) / "quarantine").glob("distinct_count_window__*"))
