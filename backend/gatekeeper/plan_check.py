# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Deterministic plan validation and normalization."""
from __future__ import annotations

from collections import Counter
from pydantic import ValidationError
from .manifest import validate_spec
from .names import clip, valid_name, violations_unique
from .policy import Policy
from .types import Plan, PlanVerdict, Violation


def check_plan(raw: object, catalog: dict, policy: Policy) -> PlanVerdict:
    if not isinstance(raw, dict):
        return PlanVerdict(violations=[Violation(code="INVALID_OUTPUT", detail="The plan must be a JSON object.")])
    if raw.get("intent") == "out_of_scope":
        return PlanVerdict(request_rejected=True, reason="The request is not about attack detection.")
    if raw.get("attack_type") == "unsupported":
        return PlanVerdict(request_rejected=True, reason="No test data is available for this attack type, so the rule cannot be validated.")
    try:
        plan = Plan.model_validate(raw)
    except ValidationError:
        return PlanVerdict(violations=[Violation(code="INVALID_OUTPUT", detail="The plan does not match the expected schema.")])
    errors = []
    def add(code, detail):
        errors.append(Violation(code=code, detail=detail))
    if plan.log_source not in catalog.get("log_sources", {}):
        add("UNKNOWN_LOG_SOURCE", "Unknown log source.")
    attacks = catalog.get("attack_types", {})
    attack = attacks.get(plan.attack_type, {})
    if attack.get("log_source") != plan.log_source:
        allowed = [key for key, value in attacks.items() if value.get("log_source") == plan.log_source]
        add("UNKNOWN_ATTACK_TYPE", f"Unknown attack type. Allowed values: {', '.join(allowed)}.")
    counts = Counter(skill.role for skill in plan.skills)
    if counts["parser"] != 1 or counts["aggregation"] != 1 or counts["enrichment"] > 2 or len(plan.skills) > 5 or len({s.name for s in plan.skills}) != len(plan.skills):
        add("PLAN_STRUCTURE", "The plan must contain one parser, one aggregation and at most two enrichments with unique names.")
    installed = {m["name"]: m for m in catalog.get("skills", [])}
    for skill in plan.skills:
        if not valid_name(skill.name):
            add("INVALID_NAME", f"Invalid skill name: {skill.name}.")
        manifest = installed.get(skill.name)
        if skill.status == "existing":
            if manifest is None:
                add("UNKNOWN_SKILL", f"The skill is not in the registry: {skill.name}.")
            elif manifest.get("kind") != skill.role:
                add("KIND_MISMATCH", f"The skill has a different kind: {skill.name}.")
        elif manifest is not None:
            if manifest.get("kind") != skill.role:
                add("SKILL_EXISTS", f"The name already belongs to a different kind: {skill.name}.")
            else:
                skill.status = "existing"
        if skill.status == "missing":
            errors.extend(validate_spec(skill.spec, skill.role, policy))
        if manifest is not None and skill.role == "parser" and plan.log_source not in manifest.get("log_sources", []):
            add("PARSER_SOURCE_MISMATCH", "The parser does not support the selected log source.")
        skill.description = clip(skill.description, 300)
    if len(plan.missing_skills) > policy.skills.max_missing_per_plan:
        add("TOO_MANY_MISSING", "The plan requests too many missing skills.")
    plan.steps = [clip(s, 200) for s in plan.steps[:10]]
    plan.goal = clip(plan.goal, 200)
    return PlanVerdict(ok=not errors, violations=violations_unique(errors), plan=plan if not errors else None)
