# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""A small declarative rule language with no identity exceptions."""
from __future__ import annotations

from copy import deepcopy
import ipaddress
import json
import math
from .manifest import forbidden_param, matches_type
from .names import clip, valid_field, valid_name
from .policy import Policy
from .types import Plan, RecipeVerdict, Violation


def canonical_json(value: object) -> str:
    result = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    result.encode("utf-8")
    return result


def display_recipe(raw: object) -> dict:
    if not isinstance(raw, dict):
        return {"name": "invalid_draft"}
    try:
        output = deepcopy(raw)
        if not valid_name(output.get("name")):
            output["name"] = "invalid_draft"
        if len(canonical_json(output)) <= 4096:
            return output
    except (TypeError, ValueError, RecursionError):
        pass
    return {"name": raw.get("name") if valid_name(raw.get("name")) else "invalid_draft"}


def number(value: object) -> bool:
    return type(value) is int or type(value) is float and math.isfinite(value)


def scalar(value: object) -> bool:
    return value is None or type(value) is bool or number(value) or isinstance(value, str) and len(value) <= 200


def ip_literal(value: object) -> bool:
    if isinstance(value, list):
        return any(ip_literal(v) for v in value)
    if not isinstance(value, str):
        return False
    try:
        ipaddress.ip_network(value, strict=False) if "/" in value else ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def check_recipe(raw: object, plan: Plan, manifests_by_name: dict[str, dict], policy: Policy) -> RecipeVerdict:
    errors: list[Violation] = []
    seen = set()
    def add(code, place, detail):
        if (code, place) not in seen:
            seen.add((code, place))
            errors.append(Violation(code=code, detail=detail))
    if not isinstance(raw, dict):
        return RecipeVerdict(violations=[Violation(code="INVALID_OUTPUT", detail="The recipe must be a JSON object.")])
    def string_keys(value):
        if isinstance(value, dict):
            return all(isinstance(k, str) and string_keys(v) for k, v in value.items())
        if isinstance(value, list):
            return all(string_keys(v) for v in value)
        return True
    if not string_keys(raw):
        return RecipeVerdict(violations=[Violation(code="RECIPE_INVALID", detail="Recipe keys must be strings.")])
    required = {"name", "attack_type", "parser", "aggregation", "condition"}
    if not required <= raw.keys() or raw.keys() - required - {"description", "enrich", "filter"}:
        add("RECIPE_INVALID", "root", "The recipe has missing or unknown fields.")
    try:
        if len(canonical_json(raw)) > policy.recipe.max_recipe_chars:
            add("RECIPE_INVALID", "size", "The recipe exceeds the size limit.")
    except (TypeError, ValueError, RecursionError):
        return RecipeVerdict(violations=[Violation(code="RECIPE_INVALID", detail="The recipe is not valid finite JSON.")])
    if not valid_name(raw.get("name")):
        add("INVALID_NAME", "name", "Invalid rule name.")
    if raw.get("attack_type") != plan.attack_type:
        add("RECIPE_INVALID", "attack_type", "The attack type does not match the plan.")
    if "description" in raw and (not isinstance(raw["description"], str) or len(raw["description"]) > 300):
        add("RECIPE_INVALID", "description", "The recipe description must contain at most 300 characters.")
    roles = {s.name: s.role for s in plan.skills}
    def get_skill(name, kind, place):
        if not isinstance(name, str) or roles.get(name) != kind or name not in manifests_by_name or manifests_by_name[name].get("kind") != kind:
            add("UNKNOWN_SKILL", place, f"Skill {name} is not in the plan as {kind}.")
            return {}
        return manifests_by_name[name]
    parser = get_skill(raw.get("parser"), "parser", "parser")
    fields = set(parser.get("outputs", [])) | {"_line", "ts"}
    enrich = raw.get("enrich", [])
    if not isinstance(enrich, list) or len(enrich) > 2:
        add("RECIPE_INVALID", "enrich", "The recipe can have at most two enrichments.")
        enrich = []
    def params_check(params, manifest, place):
        if not isinstance(params, dict):
            add("PARAM_INVALID", place, "Parameters must be an object.")
            return
        schema = manifest.get("params", {})
        for key, desc in schema.items():
            if desc.get("required") and key not in params:
                add("PARAM_INVALID", place + "." + key, f"Required parameter {key} is missing.")
        for key, value in params.items():
            pos = place + "." + key
            if forbidden_param(key, policy):
                add("RECIPE_EXCEPTION", pos, f"Parameter {key} creates a forbidden exception.")
            if key not in schema or not matches_type(value, schema[key].get("type", "")):
                add("PARAM_INVALID", pos, f"Unknown parameter or invalid type: {key}.")
                continue
            desc = schema[key]
            if number(value) and ("min" in desc and value < desc["min"] or "max" in desc and value > desc["max"]):
                add("PARAM_INVALID", pos, f"Parameter {key} is outside the allowed range.")
            if key == "window_s" and (type(value) is not int or not policy.recipe.window_s.min <= value <= policy.recipe.window_s.max):
                add("PARAM_INVALID", pos, "The time window is outside policy limits.")
            if key in {"group_by", "distinct_field", "ts_field"}:
                targets = value if isinstance(value, list) else [value]
                if not targets or any(not valid_field(t) or t not in fields for t in targets):
                    add("UNKNOWN_FIELD", pos, f"Parameter {key} references an unknown field.")
    for i, step in enumerate(enrich):
        pos = f"enrich[{i}]"
        if not isinstance(step, dict) or set(step) != {"skill", "params"}:
            add("RECIPE_INVALID", pos, "Invalid enrichment step.")
            continue
        manifest = get_skill(step["skill"], "enrichment", pos)
        params_check(step["params"], manifest, pos + ".params")
        fields.update(manifest.get("outputs", []))
    aggregation = raw.get("aggregation")
    if not isinstance(aggregation, dict) or set(aggregation) != {"skill", "params"}:
        add("RECIPE_INVALID", "aggregation", "Invalid aggregation.")
        aggregation = {}
    agg_manifest = get_skill(aggregation.get("skill"), "aggregation", "aggregation.skill")
    params_check(aggregation.get("params"), agg_manifest, "aggregation.params")
    filters = raw.get("filter", [])
    if not isinstance(filters, list) or len(filters) > policy.recipe.max_filter_conditions:
        add("RECIPE_INVALID", "filter", "Invalid filter list.")
        filters = []
    for i, condition in enumerate(filters):
        pos = f"filter[{i}]"
        if not isinstance(condition, dict) or set(condition) != {"field", "op", "value"}:
            add("RECIPE_INVALID", pos, "Invalid filter structure.")
            continue
        field, op, value = condition["field"], condition["op"], condition["value"]
        if not valid_field(field) or field not in fields:
            add("UNKNOWN_FIELD", pos, "The filter references an unknown field.")
        if op not in policy.recipe.filter_ops:
            add("RECIPE_INVALID", pos, "Unknown filter operator.")
        if op in ("in", "not_in"):
            if not isinstance(value, list) or len(value) > policy.recipe.max_in_values or not all(scalar(v) for v in value):
                add("RECIPE_INVALID", pos, "Membership requires a bounded list of scalars.")
        elif not scalar(value):
            add("RECIPE_INVALID", pos, "The filter has an invalid value.")
        if op in policy.recipe.negation_ops and (field in policy.recipe.identity_fields or ip_literal(value)):
            add("RECIPE_EXCEPTION", pos, f"The filter excludes an identity in field {field}: {clip(str(value), 50)}.")
        if op in ("eq", "in") and field in policy.recipe.ip_identity_fields:
            add("RECIPE_OVERFIT", pos, f"The filter selects a specific IP in field {field}.")
    condition = raw.get("condition")
    if not isinstance(condition, dict) or set(condition) != {"field", "op", "value"}:
        add("RECIPE_INVALID", "condition", "Invalid alert condition.")
    else:
        if not valid_field(condition["field"]) or condition["field"] not in agg_manifest.get("outputs", []):
            add("UNKNOWN_FIELD", "condition", "The condition must use an aggregation output.")
        if condition["op"] not in policy.recipe.condition_ops or not number(condition["value"]):
            add("RECIPE_INVALID", "condition", "The condition must have an allowed operator and a numeric value.")
    def scan(value, pos):
        if isinstance(value, dict):
            for key, child in value.items():
                if pos.endswith("params") and forbidden_param(key, policy):
                    add("RECIPE_EXCEPTION", pos + "." + key, f"Parameter {key} creates a forbidden exception.")
                scan(child, (pos + "." if pos else "") + key)
            if value.get("op") in policy.recipe.negation_ops and ip_literal(value.get("value")):
                add("RECIPE_EXCEPTION", pos, "The recipe excludes a specific IP address.")
        elif isinstance(value, list):
            for i, child in enumerate(value):
                scan(child, f"{pos}[{i}]")
    scan(raw, "")
    if errors:
        return RecipeVerdict(violations=errors[:10])
    return RecipeVerdict(ok=True, recipe=deepcopy(raw))


def exact_equal(left: object, right: object) -> bool:
    return type(left) is type(right) and left == right


def matches_condition(event: dict, condition: dict) -> bool:
    field, op, target = condition["field"], condition["op"], condition["value"]
    if field not in event:
        return False
    value = event[field]
    if op in ("eq", "neq"):
        return exact_equal(value, target) if op == "eq" else not exact_equal(value, target)
    if op in ("in", "not_in"):
        if not isinstance(target, list):
            return False
        found = any(exact_equal(value, t) for t in target)
        return found if op == "in" else not found
    if op in ("contains", "startswith"):
        return isinstance(value, str) and isinstance(target, str) and (target in value if op == "contains" else value.startswith(target))
    if not number(value) or not number(target):
        return False
    if op == "gt":
        return value > target
    if op == "gte":
        return value >= target
    if op == "lt":
        return value < target
    if op == "lte":
        return value <= target
    return False


def apply_filter(events: list[dict], conditions: list[dict]) -> list[dict]:
    return [event for event in events if all(matches_condition(event, cond) for cond in conditions)]
