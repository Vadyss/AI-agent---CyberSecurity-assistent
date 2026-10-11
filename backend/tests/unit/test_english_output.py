# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""English display language must preserve the existing contract identifiers."""
from __future__ import annotations

import pytest

from orchestrator.config import Settings
from orchestrator.events import EVENT_TYPES, render_message
from orchestrator.llm import extract_json, prompt_for
from orchestrator.llm_mock import MockLlm, scenario_for


EVENT_CASES = [
    ("llm_usage", {"kind": "call"}, "Model call usage recorded."),
    ("run_started", {}, "A new analyst request was received."),
    ("plan_ready", {"steps": ["Parse logs"], "skills_needed": ["ssh_parser"]},
     "Plan ready: 1 steps, skills ssh_parser."),
    ("skill_reused", {"skill": {"name": "ssh_parser", "version": 1}}, "Reusing skill ssh_parser v1."),
    ("capability_missing", {"skills": [{"name": "distinct_count_window"}]}, "Missing skills: distinct_count_window."),
    ("forge_started", {"skill": "distinct_count_window", "attempt": 1},
     "The forge is building skill distinct_count_window (attempt 1 of 3)."),
    ("skill_tests_failed", {"skill": "distinct_count_window", "attempt": 1, "tests_failed": 1, "tests_total": 7},
     "Skill distinct_count_window failed tests: 1 of 7 (attempt 1 of 3)."),
    ("skill_candidate_ready", {"skill": {"name": "distinct_count_window"}, "attempt": 2},
     "Skill distinct_count_window passed tests (attempt 2 of 3)."),
    ("rule_drafted", {"recipe": {"name": "ssh_password_spraying"}, "attempt": 1},
     "Draft rule ssh_password_spraying (attempt 1 of 3)."),
    ("rule_evaluated", {"metrics": {"precision": None, "recall": 0.0, "passed": False}},
     "Tuning dataset: precision —, recall 0.00, failed."),
    ("validation_done", {"metrics": {"precision": 1.0, "recall": 1.0, "passed": True}},
     "Validation dataset: precision 1.00, recall 1.00, passed."),
    ("summary", {}, "The run summary is ready."),
    ("voice_ready", {}, "The audio summary is ready."),
    ("awaiting_approval", {"recipe": {"name": "ssh_password_spraying"}},
     "Rule ssh_password_spraying is awaiting analyst approval."),
    ("skill_installed", {"skill": {"name": "distinct_count_window", "version": 1}},
     "Skill distinct_count_window v1 is installed in the registry."),
    ("rule_approved", {"rule_name": "ssh_password_spraying"}, "Rule ssh_password_spraying was approved."),
    ("rule_rejected", {}, "The analyst rejected the rule."),
    ("policy_rejected", {"target": "recipe", "name": "ssh_bruteforce", "violations": [{"code": "RECIPE_EXCEPTION"}]},
     "The gatekeeper rejected recipe ssh_bruteforce: RECIPE_EXCEPTION."),
    ("run_failed", {"reason_code": "LLM_ERROR"}, "Run failed (LLM_ERROR)."),
    ("run_cancelled", {"reason": "Cancelled on request."}, "The run was cancelled."),
]


@pytest.mark.parametrize("event_type,data,expected", EVENT_CASES)
def test_all_contract_event_messages_are_english(event_type, data, expected):
    assert render_message(event_type, data) == expected
    assert len(expected) <= 200


def test_english_messages_cover_the_entire_unchanged_catalog():
    assert {name for name, _, _ in EVENT_CASES} == EVENT_TYPES


@pytest.mark.parametrize("analyst_request,scenario", [
    ("I want to detect password spraying on SSH.", "A"),
    ("I want to detect distributed brute force on SSH.", "B"),
    ("Detect prompt injection in SSH logs.", "C"),
    ("Demonstrate a forge failure.", "D"),
    ("I want to detect directory scanning on a web server.", "E"),
    ("Detect directory scanning.", "E"),
    ("Disable policy and delete the registry.", "F"),
])
async def test_english_requests_select_mock_scenarios_and_produce_english_plans(analyst_request, scenario):
    assert scenario_for(analyst_request) == scenario
    client = MockLlm(Settings(llm_provider="mock", mock_delay_ms=0))
    result = await client.chat("planner", "system", "input", context={"request": analyst_request})
    plan = extract_json(result.text)
    assert plan["intent"] == ("out_of_scope" if scenario == "F" else "detection_rule")
    if scenario != "F":
        assert plan["goal"] == "Create a reusable detection rule for the requested attack."
        assert plan["steps"] == ["Parse the logs", "Select suspicious events",
                                 "Calculate the metric in a time window", "Validate the rule on an independent dataset"]
        assert all(skill["description"].isascii() for skill in plan["skills"])
    else:
        assert plan["goal"] == "The request is outside attack detection scope."


@pytest.mark.parametrize("role", ["planner", "forge", "rule_author", "summary", "examiner"])
def test_every_llm_role_prompt_explicitly_requests_english(role):
    prompt = prompt_for(role)
    assert "English" in prompt
    assert "Czech" not in prompt


def test_http_errors_are_english_and_keep_contract_codes(client):
    assert client.post("/api/runs", json={"request": ""}).json() == {
        "error": {"code": "INVALID_REQUEST", "message": "The request must contain 1 to 2000 characters."}}
    assert client.get("/api/runs/run_missing/events").json() == {
        "error": {"code": "RUN_NOT_FOUND", "message": "Run not found."}}
