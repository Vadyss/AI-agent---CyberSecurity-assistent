# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Recipe execution through the sandbox and independent incident metrics."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Callable
from .policy import Policy
from .recipe import apply_filter, canonical_json, matches_condition, number
from .types import EvalResult, Metrics, Thresholds, SandboxProtocol


class SkillExecutionError(ValueError):
    """A trusted seed produced an invalid runtime result."""


def parser_output_valid(events: object, line_count: int) -> bool:
    return isinstance(events, list) and all(isinstance(e, dict) and type(e.get("_line")) is int and 0 <= e["_line"] < line_count and number(e.get("ts")) for e in events)


def aggregation_output_valid(rows: object, events: list[dict], outputs: list[str], group_by=None, ts_field: str = "ts") -> bool:
    lines = {e["_line"] for e in events}
    if not isinstance(rows, list):
        return False
    fields = [group_by] if isinstance(group_by, str) else group_by
    groups = {}
    if fields is not None:
        for event in events:
            if all(field in event for field in fields):
                key = canonical_json({field: event[field] for field in fields})
                groups.setdefault(key, []).append(event)
    for row in rows:
        if not (isinstance(row, dict) and isinstance(row.get("group"), dict)
                and number(row.get("window_start")) and number(row.get("window_end")) and row["window_start"] <= row["window_end"]
                and isinstance(row.get("_lines"), list) and len(row["_lines"]) <= 500
                and all(type(line) is int and line in lines for line in row["_lines"])
                and all(number(row.get(field)) for field in outputs)):
            return False
        if fields is not None:
            if set(row["group"]) != set(fields):
                return False
            try:
                members = groups.get(canonical_json(row["group"]))
            except (ValueError, TypeError):
                return False
            if members is None:
                return False
            allowed_lines = {event["_line"] for event in members if number(event.get(ts_field)) and row["window_start"] <= event[ts_field] <= row["window_end"]}
            if not set(row["_lines"]) <= allowed_lines:
                return False
    return True


def merge_incidents(alerts: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for alert in alerts:
        groups[canonical_json(alert["group"])].append(alert)
    incidents = []
    for key in sorted(groups):
        current = None
        for alert in sorted(groups[key], key=lambda r: (r["window_start"], r["window_end"])):
            if current is None or alert["window_start"] > current["window_end"]:
                current = {"group": deepcopy(alert["group"]), "window_start": alert["window_start"], "window_end": alert["window_end"], "_lines": sorted(set(alert["_lines"])), "rows": [alert]}
                incidents.append(current)
            else:
                current["window_end"] = max(current["window_end"], alert["window_end"])
                current["_lines"] = sorted(set(current["_lines"]) | set(alert["_lines"]))
                current["rows"].append(alert)
    return incidents


def calculate_metrics(incidents: list[dict], labels: dict, attack_type: str, thresholds) -> tuple[Metrics, int]:
    instances = labels["instances"]
    targets = [i for i in instances if i["attack_type"] == attack_type]
    if not targets:
        raise ValueError("The dataset has no target attack instance.")
    all_attack_lines = set().union(*(set(i["lines"]) for i in instances))
    detected_lines = set().union(*(set(i["_lines"]) for i in incidents)) if incidents else set()
    target_lines = set().union(*(set(i["lines"]) for i in targets))
    tp = sum(bool(set(i["lines"]) & detected_lines) for i in targets)
    fp = sum(not (set(i["_lines"]) & all_attack_lines) for i in incidents)
    cross_type_hits = sum(bool(set(i["_lines"]) & all_attack_lines) and not bool(set(i["_lines"]) & target_lines) for i in incidents)
    fn = len(targets) - tp
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    minimum = thresholds.model_dump() if hasattr(thresholds, "model_dump") else thresholds
    passed = precision is not None and recall is not None and precision >= minimum["min_precision"] and recall >= minimum["min_recall"]
    return Metrics(true_positives=tp, false_positives=fp, false_negatives=fn, precision=precision, recall=recall,
                   thresholds=Thresholds(**minimum), passed=passed), cross_type_hits


def feedback_examples(incidents: list[dict], rows: list[dict], events: list[dict], labels: dict, recipe: dict) -> list[str]:
    detected = set().union(*(set(i["_lines"]) for i in incidents)) if incidents else set()
    targets = [i for i in labels["instances"] if i["attack_type"] == recipe["attack_type"]]
    all_attack_lines = set().union(*(set(i["lines"]) for i in labels["instances"]))
    metric = recipe["condition"]["field"]
    examples = []
    missed = [i for i in targets if not set(i["lines"]) & detected][:3]
    for instance in missed:
        line_set = set(instance["lines"])
        relevant_events = [e for e in events if e["_line"] in line_set]
        times = [e["ts"] for e in relevant_events]
        duration = round(max(times) - min(times)) if times else 0
        peak = max((r.get(metric, 0) for r in rows if set(r["_lines"]) & line_set), default=0)
        users = len({e.get("user") for e in relevant_events if isinstance(e.get("user"), str)})
        ips = len({e.get("src_ip") for e in relevant_events if isinstance(e.get("src_ip"), str)})
        examples.append(f"Attack type {recipe['attack_type']}: {len(line_set)} lines over {duration} s, {users} distinct users, {ips} distinct IPs; peak metric in its window: {peak}.")
    false_alerts = [i for i in incidents if not set(i["_lines"]) & all_attack_lines][:3]
    for incident in false_alerts:
        group_fields = recipe["aggregation"]["params"]["group_by"]
        group_fields = [group_fields] if isinstance(group_fields, str) else group_fields
        fields = ", ".join(group_fields)
        peak = max((r.get(metric, 0) for r in incident["rows"]), default=0)
        examples.append(f"Alert on normal traffic: group by {fields}, peak value {metric} = {peak}, {len(incident['_lines'])} lines.")
    return examples


async def evaluate_recipe(recipe: dict, lines: list[str], labels: dict,
                          skill_loader: Callable[[str], tuple[str, dict, dict]], sandbox: SandboxProtocol,
                          policy: Policy, *, feedback: bool = False, audit=None, run_id: str | None = None) -> EvalResult:
    async def run(name, inputs, params, validator):
        code, manifest, meta = skill_loader(name)
        if len(canonical_json(inputs).encode("utf-8")) > policy.sandbox.max_input_bytes:
            raise ValueError("Sandbox input exceeds the size limit.")
        result = await sandbox.run(code, inputs, params, allowed_imports=list(policy.skills.allowed_imports), timeout_s=policy.sandbox.run_timeout_s)
        valid = result.get("status") == "ok" and validator(result.get("result"), manifest)
        if valid:
            return result["result"]
        if audit is not None:
            audit.append("integrity_violation", run_id, {"skill": name, "reason": "Invalid skill output during evaluation."})
        if meta.get("origin") == "seed":
            raise SkillExecutionError("A seed skill returned invalid output.")
        return None
    events = await run(recipe["parser"], lines, {}, lambda output, _: parser_output_valid(output, len(lines)))
    if events is None:
        metrics, cross = calculate_metrics([], labels, recipe["attack_type"], policy.thresholds)
        return EvalResult(metrics=metrics, cross_type_hits=cross)
    for step in recipe.get("enrich", []):
        original = deepcopy(events)
        def valid_enrich(output, manifest):
            return isinstance(output, list) and len(output) == len(original) and all(isinstance(enriched, dict) and all(k in enriched and enriched[k] == v for k, v in old.items()) and all(field in enriched for field in manifest["outputs"]) for old, enriched in zip(original, output))  # noqa: B023 - called within this iteration only
        events = await run(step["skill"], events, step["params"], valid_enrich)
        if events is None:
            metrics, cross = calculate_metrics([], labels, recipe["attack_type"], policy.thresholds)
            return EvalResult(metrics=metrics, cross_type_hits=cross)
    unfiltered = events
    events = apply_filter(events, recipe.get("filter", []))
    rows = await run(recipe["aggregation"]["skill"], events, recipe["aggregation"]["params"], lambda output, manifest: aggregation_output_valid(output, events, manifest["outputs"], recipe["aggregation"]["params"]["group_by"], recipe["aggregation"]["params"].get("ts_field", "ts")))
    rows = rows or []
    incidents = merge_incidents([r for r in rows if matches_condition(r, recipe["condition"])])
    metrics, cross = calculate_metrics(incidents, labels, recipe["attack_type"], policy.thresholds)
    examples = feedback_examples(incidents, rows, unfiltered, labels, recipe) if feedback else []
    return EvalResult(metrics=metrics, feedback_examples=examples, cross_type_hits=cross)
