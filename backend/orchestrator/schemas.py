# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Shape of each role's JSON output, checked at the model boundary.

These schemas check structure only: required keys and their types. Whether
a plan, tool or rule is acceptable is decided by the deterministic
gatekeeper. A shape error earns the model one targeted repair request.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gatekeeper.types import Plan


class Shape(BaseModel):
    # Unknown keys are left for the gatekeeper to judge.
    model_config = ConfigDict(extra="allow")


class ForgeOutput(Shape):
    manifest: dict[str, Any]
    code: str
    tests: str


class RuleAuthorOutput(Shape):
    recipe: dict[str, Any]
    explanation: str | None = None


class SummaryOutput(Shape):
    text: str = Field(min_length=1)


# The planner's shape is exactly the gatekeeper's plan schema, so the repair
# never refuses a plan the gatekeeper itself would accept.
SCHEMAS: dict[str, type[BaseModel]] = {"planner": Plan, "forge": ForgeOutput,
                                       "rule_author": RuleAuthorOutput, "summary": SummaryOutput}


def shape_problems(role: str, value: dict) -> list[str]:
    """Paths and reasons only, never the offending values, so nothing is echoed back."""
    schema = SCHEMAS.get(role)
    if schema is None:
        return []
    if role == "planner" and (value.get("intent") == "out_of_scope" or value.get("attack_type") == "unsupported"):
        return []  # The gatekeeper rejects these requests before it validates the plan.
    try:
        schema.model_validate(value)
    except ValidationError as error:
        return [f"{'.'.join(str(part) for part in issue['loc']) or '(root)'}: {issue['msg']}"
                for issue in error.errors(include_url=False, include_input=False)[:10]]
    return []
