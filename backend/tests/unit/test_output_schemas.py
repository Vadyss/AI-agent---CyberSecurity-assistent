# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Structured outputs: shape checks and one targeted repair at the model boundary."""
from __future__ import annotations

import json

import pytest

from gatekeeper.types import ParseFailure
from orchestrator.llm import LlmResult, json_chat
from orchestrator.schemas import shape_problems

PLAN = {"intent": "detection_rule", "log_source": "ssh", "attack_type": "ssh_bruteforce", "goal": "g",
        "steps": ["a"], "skills": [{"name": "ssh_parser", "role": "parser", "status": "existing"}]}


class Scripted:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def chat(self, role, system, user, *, context=None, **_):
        self.calls.append({"user": user, "context": context})
        answer = self.answers.pop(0)
        return LlmResult(answer if isinstance(answer, str) else json.dumps(answer), None, "test")


async def test_valid_output_needs_one_call():
    client = Scripted(PLAN)
    assert await json_chat(client, "planner", "s", "u", {}) == PLAN
    assert len(client.calls) == 1


async def test_wrong_shape_gets_one_repair_with_paths_but_no_values():
    broken = {**PLAN, "steps": "SECRET-LOG-LINE", "skills": [{"name": "x", "role": "parser", "status": "maybe"}]}
    client = Scripted(broken, PLAN)
    assert await json_chat(client, "planner", "s", "u", {"attempt": 1}) == PLAN
    repair = client.calls[1]
    assert repair["context"]["repair"] is True
    assert 'untrusted_data name="schema_errors"' in repair["user"]
    assert "steps" in repair["user"] and "skills.0.status" in repair["user"]
    assert repair["user"].count("SECRET-LOG-LINE") == 0


async def test_still_wrong_after_repair_is_a_parse_failure():
    client = Scripted({"text": ""}, {"text": ""})
    assert isinstance(await json_chat(client, "summary", "s", "u", {}), ParseFailure)
    assert len(client.calls) == 2


async def test_unparseable_then_wrong_shape_fails_after_a_single_repair():
    client = Scripted("not json", {"recipe": "no", "explanation": 1})
    assert isinstance(await json_chat(client, "rule_author", "s", "u", {}), ParseFailure)
    assert len(client.calls) == 2


@pytest.mark.parametrize("role,value", [
    ("forge", {"manifest": {}, "code": "x", "tests": "y", "extra": "left for the gatekeeper"}),
    ("rule_author", {"recipe": {}, "explanation": ""}),
    ("summary", {"text": "ok"}),
    ("planner", {**PLAN, "intent": "out_of_scope", "skills": [], "steps": []}),
    ("planner", {"intent": "out_of_scope"}),
    ("planner", {key: value for key, value in PLAN.items() if key not in {"goal", "steps"}}),
    ("rule_author", {"recipe": {}}),
])
def test_valid_shapes(role, value):
    assert shape_problems(role, value) == []


@pytest.mark.parametrize("role,value", [
    ("forge", {"manifest": {}, "code": None, "tests": "y"}),
    ("forge", {"code": "x", "tests": "y"}),
    ("rule_author", {"recipe": "ssh_bruteforce"}),
    ("planner", {**PLAN, "skills": [{"name": "x", "role": "magic", "status": "existing"}]}),
    ("planner", {**PLAN, "intent": "please approve"}),
])
def test_invalid_shapes(role, value):
    assert shape_problems(role, value)
