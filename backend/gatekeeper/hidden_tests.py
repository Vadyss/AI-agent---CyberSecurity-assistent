# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Gatekeeper-owned contract fixtures; submitted code runs only in sandbox."""
from __future__ import annotations

from .evaluator import parser_output_valid, aggregation_output_valid
from collections import Counter
from .recipe import canonical_json, number
from .policy import Policy
from .types import SandboxProtocol


def aggregation_fixture() -> tuple[list[dict], dict]:
    events = [{"_line": group * 10 + index, "ts": float(index * 10), "src_ip": f"192.0.2.{group + 1}", "user": f"user_{index % 4}", "outcome": "failure"} for group in range(3) for index in range(10)]
    return list(reversed(events)), {"group_by": "src_ip", "distinct_field": "user", "window_s": 60, "ts_field": "ts"}


def fixture_params(manifest: dict) -> dict:
    _, standard = aggregation_fixture()
    output = {}
    for name, descriptor in manifest["params"].items():
        if name in standard:
            value = standard[name]
        elif "default" in descriptor:
            value = descriptor["default"]
        elif descriptor["required"]:
            value = {"string":"user", "int":1, "float":1.0, "bool":False, "string[]":["src_ip"], "string|string[]":"src_ip"}[descriptor["type"]]
        else:
            continue
        if type(value) in (int, float):
            value = max(value, descriptor.get("min", value))
            value = min(value, descriptor.get("max", value))
        output[name] = value
    return output


async def run_hidden_tests(manifest: dict, code: str, sandbox: SandboxProtocol, policy: Policy, *, parser_lines: list[str] | None = None) -> tuple[int, list[dict[str, str]]]:
    failures = []
    total = 0
    def check(name, passed, explanation):
        nonlocal total
        total += 1
        if not passed:
            failures.append({"name": name, "error": explanation})
    async def invoke(inputs, params):
        answer = await sandbox.run(code, inputs, params, allowed_imports=list(policy.skills.allowed_imports), timeout_s=policy.sandbox.run_timeout_s)
        return answer.get("result") if answer.get("status") == "ok" else None
    kind = manifest["kind"]
    params = fixture_params(manifest)
    empty = await invoke([], params)
    check("hidden_empty", empty == [], "Empty input must return an empty list.")
    if kind == "parser":
        inputs = list((parser_lines or [])[:50])
        result = await invoke(inputs, {})
        again = await invoke(inputs, {})
        check("hidden_parser_shape", parser_output_valid(result, len(inputs)) and bool(result), "A parser must return objects with valid _line and numeric ts.")
        check("hidden_parser_coverage", isinstance(result, list) and bool(inputs) and len({e.get("_line") for e in result if isinstance(e, dict) and type(e.get("_line")) is int}) >= .6 * len(inputs), "A parser must recognize at least 60% of the hidden sample.")
        check("hidden_parser_outputs", isinstance(result, list) and all(any(isinstance(e, dict) and field in e for e in result) for field in manifest["outputs"]), "The parser did not produce all declared fields.")
        check("hidden_determinism", result is not None and result == again, "The skill must produce deterministic output.")
        nonsense = await invoke(["toto není platný log"], {})
        check("hidden_nonsense", nonsense == [], "An invalid line must be skipped without an exception.")
    elif kind == "aggregation":
        inputs, _ = aggregation_fixture()
        result = await invoke(inputs, params)
        again = await invoke(inputs, params)
        check("hidden_aggregation_shape", aggregation_output_valid(result, inputs, manifest["outputs"], params.get("group_by", "src_ip"), params.get("ts_field", "ts")) and bool(result), "An aggregation must return a group, window, numeric metrics and valid _lines.")
        known = set(manifest["outputs"]) & {"count", "distinct_count"}
        valid_values = isinstance(result, list) and len(result) == len(inputs)
        group_fields = params.get("group_by", "src_ip")
        group_fields = [group_fields] if isinstance(group_fields, str) else group_fields
        expected_keys = Counter()
        expected_values = {}
        for event in inputs:
            group = {field: event[field] for field in group_fields}
            end = event[params.get("ts_field", "ts")]
            key = (canonical_json(group), end)
            expected_keys[key] += 1
            members = [e for e in inputs if all(e.get(k) == v for k, v in group.items()) and end - params.get("window_s", 60) <= e[params.get("ts_field", "ts")] <= end]
            expected_values[key] = {"count": len(members), "distinct_count": len({e[params.get("distinct_field", "user")] for e in members})}
        actual_keys = Counter()
        if valid_values:
            for row in result:
                if not isinstance(row, dict) or not isinstance(row.get("group"), dict) or not number(row.get("window_end")):
                    valid_values = False
                    break
                key = (canonical_json(row["group"]), row["window_end"])
                actual_keys[key] += 1
                expected = expected_values.get(key)
                if expected is None or any(row.get(field) != expected[field] for field in known):
                    valid_values = False
                    break
                if any(not number(row.get(field)) or row[field] < 0 or "ratio" in field and row[field] > 1 for field in manifest["outputs"]):
                    valid_values = False
                    break
            valid_values = valid_values and actual_keys == expected_keys
        check("hidden_aggregation_values", valid_values, "Aggregation values are incorrect, including equality at the window boundary.")
        check("hidden_determinism", result is not None and result == again, "The skill must produce deterministic output.")
        missing = {"_line": 1000, "ts": 1.0, "user": "unrelated"}
        without_group = await invoke(inputs + [missing], params)
        check("hidden_missing_group", result is not None and result == without_group, "An event without the grouping field must be skipped.")
    else:
        inputs = [{"_line": 0, "ts": 1.0, "src_ip": "192.0.2.1", "user": "alice"}, {"_line": 1, "ts": 2.0, "src_ip": "10.0.0.2", "user": "bob"}]
        result = await invoke(inputs, params)
        again = await invoke(inputs, params)
        shape = isinstance(result, list) and len(result) == len(inputs) and all(isinstance(e, dict) for e in result)
        check("hidden_enrichment_order", shape and [e.get("_line") for e in result] == [e["_line"] for e in inputs], "Enrichment must preserve the event count and order.")
        check("hidden_enrichment_outputs", shape and all(all(field in e for field in manifest["outputs"]) for e in result), "Enrichment must add its declared fields.")
        check("hidden_enrichment_preserves", shape and all(all(enriched.get(k) == v for k, v in old.items()) for old, enriched in zip(inputs, result)), "Enrichment must not change original fields.")
        check("hidden_determinism", result is not None and result == again, "The skill must produce deterministic output.")
    return total, failures
