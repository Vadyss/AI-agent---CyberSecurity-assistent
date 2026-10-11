# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Deterministic proposals; real gatekeeper and sandbox decide all outcomes."""
from __future__ import annotations

import asyncio
import json
from functools import lru_cache
from pathlib import Path

from .llm import LlmError, LlmResult, count_call, track_mock_call
from .usage import UsageLedger

FIXTURES = Path(__file__).resolve().parent / "mock_fixtures"


def scenario_for(request: str, override: str = "") -> str:
    if override:
        return override
    text = request.lower()
    for words, scenario in ((["spray"], "A"), (["distrib"], "B"), (["inject"], "C"), (["fail"], "D"),
                            (["web", "directory", "scan", "adresář", "skenov"], "E"),
                            (["policy", "disable", "delete", "politik", "vypni", "smaž"], "F")):
        if any(word in text for word in words):
            return scenario
    return "A"


@lru_cache(maxsize=20)
def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def manifest(name: str) -> dict:
    params = {"group_by": {"type": "string|string[]", "required": True},
              "window_s": {"type": "int", "required": True, "min": 10, "max": 86400},
              "ts_field": {"type": "string", "required": False, "default": "ts"}}
    parser = name == "nginx_access_parser"
    if name == "distinct_count_window":
        params["distinct_field"] = {"type": "string", "required": True}
    result = {"name": name, "version": 1, "kind": "parser" if parser else "aggregation",
              "description": "Parses nginx combined access logs into events." if parser else
              "Counts distinct field values in a sliding time window for each group." if name == "distinct_count_window" else
              "Calculates the failure ratio in a sliding time window for each group.",
              "entrypoint": "run", "inputs": "lines" if parser else "events",
              "outputs": ["src_ip", "method", "path", "protocol", "status", "bytes", "referer", "user_agent"] if parser else
              ["distinct_count"] if name == "distinct_count_window" else ["failure_ratio"],
              "params": {} if parser else params,
              "imports": ["re", "datetime"] if parser else ["collections"] if name == "distinct_count_window" else [],
              "permissions": {"network": False, "filesystem": False, "subprocess": False}}
    if parser:
        result["log_sources"] = ["web"]
    return result


class MockLlm:
    def __init__(self, settings):
        self.settings = settings
        self.usage = UsageLedger()

    @track_mock_call
    async def chat(self, role, system, user, *, max_tokens=None, temperature=0.2, context=None):
        count_call(self.settings.llm_max_calls_per_run)
        if self.settings.mock_delay_ms:
            await asyncio.sleep(self.settings.mock_delay_ms / 1000)
        context = context or {}
        scenario = scenario_for(context.get("request", ""), self.settings.mock_scenario)
        attempt = context.get("attempt", 1)
        if role == "planner":
            output = self._plan(scenario, context)
        elif role == "forge":
            spec = context.get("spec", {})
            name = spec["name"]
            code = fixture(f"{name}.py")
            if name == "distinct_count_window":
                tests = fixture("distinct_tests.py")
                if scenario == "A" and attempt == 1:
                    code = fixture("distinct_count_window_bad.py")
            elif name == "failure_ratio_window":
                tests = fixture("failure_ratio_tests.py")
                if attempt == 2:
                    code = "import socket\n" + code
            elif name == "nginx_access_parser":
                tests = fixture("nginx_tests.py")
            else:
                raise LlmError("The mock does not support the requested skill.")
            output = {"manifest": manifest(name), "code": code, "tests": tests}
        elif role in {"rule_author", "rule"}:
            output = self._recipe(scenario, context, attempt)
        elif role in {"summary", "summarizer"}:
            from .summarizer import fallback_summary
            output = {"text": fallback_summary(context["plan"], context["recipe"], context["metrics_tuning"],
                                                context["metrics_validation"], context["stats"])}
        else:
            raise LlmError("The mock does not support the requested role.")
        return LlmResult(json.dumps(output, ensure_ascii=False), None, "mock")

    def _plan(self, scenario, context):
        if scenario == "F":
            return {"intent": "out_of_scope", "log_source": "ssh", "attack_type": "unsupported", "skills": [],
                    "goal": "The request is outside attack detection scope.", "steps": []}
        source = "web" if scenario == "E" else "ssh"
        attack = {"A": "ssh_password_spraying", "B": "ssh_distributed_bruteforce", "C": "ssh_bruteforce",
                  "D": "ssh_password_spraying", "E": "web_dir_bruteforce"}[scenario]
        names = ["nginx_access_parser", "count_window"] if scenario == "E" else ["ssh_parser",
                 "count_window" if scenario == "C" else "failure_ratio_window" if scenario == "D" else "distinct_count_window"]
        installed = {item["name"] for item in context.get("catalog", {}).get("skills", [])}
        skills = []
        for name in names:
            role = "parser" if name.endswith("parser") else "aggregation"
            skill = {"name": name, "role": role, "status": "existing" if name in installed else "missing"}
            if skill["status"] == "missing":
                details = manifest(name)
                skill["description"] = details["description"]
                skill["spec"] = {"inputs": details["inputs"], "params": {key: param["type"] for key, param in details["params"].items()},
                                 "outputs": details["outputs"], "behavior": "Parse combined access logs." if role == "parser" else
                                 "For each event, compute the requested metric in its inclusive sliding window for the group."}
            skills.append(skill)
        return {"intent": "detection_rule", "log_source": source, "attack_type": attack,
                "goal": "Create a reusable detection rule for the requested attack.",
                "steps": ["Parse the logs", "Select suspicious events", "Calculate the metric in a time window", "Validate the rule on an independent dataset"],
                "skills": skills}

    def _recipe(self, scenario, context, attempt):
        plan = context["plan"]
        # A prior verified rule shortens the next run to a single rule attempt.
        if context.get("prior") and scenario != "C":
            prior = context["prior"]
            recipe = prior.get("recipe", prior)
            return {"recipe": recipe, "explanation": "Reusing a previously approved rule and validating its results again."}
        recipe = {"name": plan["attack_type"], "attack_type": plan["attack_type"],
                  "parser": "nginx_access_parser" if scenario == "E" else "ssh_parser",
                  "filter": [{"field": "status" if scenario == "E" else "outcome", "op": "eq", "value": 404 if scenario == "E" else "failure"}],
                  "aggregation": {"skill": "count_window" if scenario in {"C", "E"} else "distinct_count_window",
                                  "params": {"group_by": "user" if scenario == "B" else "src_ip", "window_s": 300}},
                  "condition": {"field": "count" if scenario in {"C", "E"} else "distinct_count", "op": "gte",
                                "value": 20 if scenario == "E" else 10 if scenario == "C" else 25 if scenario == "A" and attempt == 1 else 5}}
        if scenario in {"A", "B"}:
            recipe["aggregation"]["params"]["distinct_field"] = "src_ip" if scenario == "B" else "user"
        if scenario == "C" and attempt == 1:
            recipe["filter"].append({"field": "src_ip", "op": "neq", "value": "198.51.100.23"})
        return {"recipe": recipe, "explanation": "Detecting an unusual number of failed events in a five-minute window."}

    async def close(self):
        pass
