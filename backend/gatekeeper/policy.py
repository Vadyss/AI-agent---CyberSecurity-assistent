# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Read-only, deeply immutable configuration of deterministic authority."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


# Attributes that expose interpreter internals without a leading underscore.
# The sandbox runner enforces the same set at runtime.
BASELINE_FORBIDDEN_ATTRIBUTES = frozenset(
    "gi_frame gi_code gi_yieldfrom cr_frame cr_code cr_await ag_frame ag_code ag_await f_back f_globals "
    "f_locals f_builtins f_code f_trace tb_frame tb_next co_code co_consts co_names func_globals func_code "
    "format_map".split())


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Authority(Frozen):
    note: str


class Attempts(Frozen):
    plan: Literal[3]
    forge_per_skill: Literal[3]
    rule: Literal[3]


class ThresholdPolicy(Frozen):
    min_precision: float = Field(gt=0, le=1)
    min_recall: float = Field(gt=0, le=1)


class DatasetPolicy(Frozen):
    min_instances_per_attack_type: int = Field(ge=1)
    sample_lines: int = Field(ge=1, le=20)


class Permissions(Frozen):
    network: Literal["deny"]
    filesystem: Literal["deny"]
    subprocess: Literal["deny"]


class SkillPolicy(Frozen):
    allowed_kinds: tuple[Literal["parser", "aggregation", "enrichment"], ...]
    max_missing_per_plan: int = Field(ge=0, le=2)
    min_llm_tests: int = Field(ge=4)
    max_code_bytes: int = Field(gt=0, le=20000)
    max_test_bytes: int = Field(gt=0, le=20000)
    max_ast_nodes: int = Field(gt=0, le=6000)
    max_string_literal: int = Field(gt=0, le=2000)
    allowed_imports: tuple[str, ...]
    forbidden_calls: tuple[str, ...]
    forbid_dunder_access: Literal[True]
    forbidden_attributes: tuple[str, ...]
    permissions: Permissions
    network_allowed_domains: tuple[str, ...]
    forbidden_param_patterns: tuple[str, ...]

    @model_validator(mode="after")
    def protect_baseline(self):
        safe = set("re datetime collections itertools functools math statistics ipaddress json typing dataclasses bisect heapq string operator enum decimal fractions hashlib base64 binascii".split())
        forbidden = set("eval exec compile open __import__ input breakpoint globals locals vars getattr setattr delattr dir help exit quit memoryview print".split())
        if not set(self.allowed_imports) <= safe or not forbidden <= set(self.forbidden_calls):
            raise ValueError("The policy weakens forbidden imports or calls.")
        if not BASELINE_FORBIDDEN_ATTRIBUTES <= set(self.forbidden_attributes):
            raise ValueError("The policy weakens the introspection prohibition.")
        if self.network_allowed_domains or not {"exclude", "ignore", "whitelist", "allowlist", "skip"} <= set(self.forbidden_param_patterns):
            raise ValueError("The policy weakens the network or exception prohibition.")
        return self


class SandboxPolicy(Frozen):
    test_timeout_s: int = Field(gt=0, le=30)
    run_timeout_s: int = Field(gt=0, le=30)
    max_input_bytes: int = Field(gt=0, le=5000000)
    max_output_bytes: int = Field(gt=0, le=10000000)


class WindowPolicy(Frozen):
    min: int = Field(ge=10)
    max: int = Field(le=86400)

    @model_validator(mode="after")
    def ordered(self):
        if self.min > self.max:
            raise ValueError("Invalid window range.")
        return self


class RecipePolicy(Frozen):
    filter_ops: tuple[Literal["eq", "neq", "in", "not_in", "gt", "gte", "lt", "lte", "contains", "startswith"], ...]
    condition_ops: tuple[Literal["gt", "gte", "lt", "lte", "eq"], ...]
    negation_ops: tuple[Literal["neq", "not_in"], ...]
    identity_fields: tuple[str, ...]
    ip_identity_fields: tuple[str, ...]
    max_filter_conditions: int = Field(ge=0, le=10)
    max_in_values: int = Field(gt=0, le=50)
    window_s: WindowPolicy
    max_recipe_chars: int = Field(gt=0, le=4096)

    @model_validator(mode="after")
    def protect_identities(self):
        if not {"src_ip", "dst_ip", "user", "username", "host", "src_host"} <= set(self.identity_fields):
            raise ValueError("Protected identities are missing.")
        if not {"src_ip", "dst_ip"} <= set(self.ip_identity_fields) or set(self.negation_ops) != {"neq", "not_in"}:
            raise ValueError("IP address protection is missing.")
        return self


class LlmPolicy(Frozen):
    max_calls_per_run: int = Field(gt=0, le=25)


class Policy(Frozen):
    version: Literal[1]
    authority: Authority
    attempts: Attempts
    thresholds: ThresholdPolicy
    datasets: DatasetPolicy
    skills: SkillPolicy
    sandbox: SandboxPolicy
    recipe: RecipePolicy
    llm: LlmPolicy
    sha256: str = ""

    def digest_for_llm(self) -> dict:
        return {"allowed_imports": list(self.skills.allowed_imports),
                "forbidden_calls": list(self.skills.forbidden_calls),
                "forbidden_attributes": list(self.skills.forbidden_attributes),
                "forbidden_param_patterns": list(self.skills.forbidden_param_patterns),
                "filter_ops": list(self.recipe.filter_ops), "condition_ops": list(self.recipe.condition_ops),
                "max_code_bytes": self.skills.max_code_bytes, "max_test_bytes": self.skills.max_test_bytes,
                "min_llm_tests": self.skills.min_llm_tests, "window_s": self.recipe.window_s.model_dump(),
                "permissions": self.skills.permissions.model_dump()}


def load_policy(path: Path) -> Policy:
    payload = Path(path).read_bytes()
    raw = yaml.safe_load(payload)
    if not isinstance(raw, dict) or "sha256" in raw:
        raise ValueError("Invalid policy structure.")
    return Policy.model_validate({**raw, "sha256": hashlib.sha256(payload).hexdigest()})


def policy_digest_for_llm(policy: Policy) -> dict:
    return policy.digest_for_llm()
