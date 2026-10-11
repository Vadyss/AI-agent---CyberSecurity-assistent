# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Shared JSON contracts and interfaces; safe for the orchestrator to import."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

SkillKind = Literal["parser", "aggregation", "enrichment"]
Catalog = dict[str, Any]
Recipe = dict[str, Any]


class JsonModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillInfo(JsonModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    version: int = Field(ge=1)
    kind: SkillKind
    description: str
    origin: Literal["seed", "agent"]
    status: Literal["candidate", "installed"]
    created_by_run: str | None
    created_at: str

    @field_validator("description", mode="before")
    @classmethod
    def clean_description(cls, value: str) -> str:
        value = "".join(c for c in value if c in "\n\t" or not unicodedata.category(c).startswith("C"))
        return value if len(value) <= 300 else value[:299] + "…"


class Thresholds(JsonModel):
    min_precision: float = Field(ge=0, le=1)
    min_recall: float = Field(ge=0, le=1)


class Metrics(JsonModel):
    true_positives: int = Field(ge=0)
    false_positives: int = Field(ge=0)
    false_negatives: int = Field(ge=0)
    precision: float | None = Field(ge=0, le=1)
    recall: float | None = Field(ge=0, le=1)
    thresholds: Thresholds
    passed: bool

    @field_validator("precision", "recall")
    @classmethod
    def round_metric(cls, value: float | None) -> float | None:
        return None if value is None else round(value, 3)


class Violation(JsonModel):
    code: str
    detail: str

    @field_validator("detail", mode="before")
    @classmethod
    def clip_detail(cls, value: str) -> str:
        value = "".join(c for c in value if c in "\n\t" or not unicodedata.category(c).startswith("C"))
        return value if len(value) <= 200 else value[:199] + "…"


class PlanSkill(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    role: SkillKind
    status: Literal["existing", "missing"]
    description: str = ""
    spec: dict[str, Any] = Field(default_factory=dict)


MissingSkill = PlanSkill


class Plan(BaseModel):
    model_config = ConfigDict(extra="ignore")
    intent: Literal["detection_rule", "out_of_scope"]
    log_source: str
    attack_type: str
    goal: str = ""
    steps: list[str] = Field(default_factory=list)
    skills: list[PlanSkill] = Field(default_factory=list)
    custom_attack: dict[str, Any] | None = None

    @property
    def existing_skills(self) -> list[PlanSkill]:
        return [s for s in self.skills if s.status == "existing"]

    @property
    def missing_skills(self) -> list[PlanSkill]:
        return [s for s in self.skills if s.status == "missing"]


@dataclass(frozen=True)
class ParseFailure:
    reason: str


@dataclass(frozen=True)
class GatekeeperConfig:
    data_dir: Path
    datasets_dir: Path
    policy_path: Path
    seed_skills_dir: Path
    examiner_enabled: bool = False


@dataclass
class PlanVerdict:
    ok: bool = False
    request_rejected: bool = False
    reason: str = ""
    violations: list[Violation] = field(default_factory=list)
    plan: Plan | None = None


@dataclass
class RecipeVerdict:
    ok: bool = False
    violations: list[Violation] = field(default_factory=list)
    recipe: Recipe | None = None


@dataclass
class SkillVerdict:
    kind: Literal["policy_rejected", "tests_failed", "candidate"]
    violations: list[Violation] = field(default_factory=list)
    tests_total: int = 0
    tests_failed: int = 0
    error_excerpt: str = ""
    failures: list[dict[str, str]] = field(default_factory=list)
    skill_info: SkillInfo | None = None
    manifest: dict[str, Any] | None = None
    code_sha256: str = ""


@dataclass
class EvalResult:
    metrics: Metrics
    feedback_examples: list[str] = field(default_factory=list)
    cross_type_hits: int = 0


class SandboxError(Exception):
    """Transport/protocol failure, distinct from a failed skill job."""


class SandboxProtocol(Protocol):
    async def health(self) -> bool: ...
    async def execute(self, job: dict[str, Any]) -> dict[str, Any]: ...
    async def run(self, code: str, inputs: list, params: dict, *,
                  allowed_imports: list[str], timeout_s: float = 15) -> dict[str, Any]: ...
    async def test(self, code: str, tests: str, *,
                   allowed_imports: list[str], timeout_s: float = 10) -> dict[str, Any]: ...
    async def close(self) -> None: ...
