# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""The exact frontend contract v1, including every event payload."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationInfo, field_validator

from gatekeeper.types import Metrics, SkillInfo, Thresholds, Violation
from orchestrator.text import clip

Phase = Literal["intake", "plan", "forge", "rule", "validation", "approval", "done"]
Status = Literal["queued", "running", "awaiting_approval", "approved", "rejected", "failed", "cancelled"]
ReasonCode = Literal["REQUEST_REJECTED", "PLAN_INVALID", "FORGE_FAILED", "RULE_FAILED",
                     "VALIDATION_FAILED", "LLM_ERROR", "SANDBOX_ERROR", "INTERNAL_ERROR"]
Name = str


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def display_limits(cls, value: Any, info: ValidationInfo) -> Any:
        limits = {"text": 1000, "explanation": 1000, "error_excerpt": 500,
                  "description": 300, "detail": 200, "reason": 500, "comment": 500}
        if info.field_name in limits and isinstance(value, str):
            return clip(value, limits[info.field_name])
        if info.field_name == "steps" and isinstance(value, list):
            return [clip(v, 200) if isinstance(v, str) else v for v in value[:10]]
        if info.field_name == "violations" and isinstance(value, list):
            return value[:10]
        return value


class RunInfo(ContractModel):
    run_id: str = Field(pattern=r"^run_[a-z0-9]{4,32}$")
    request: str
    status: Status
    created_at: str
    finished_at: str | None
    last_seq: int = Field(ge=0)
    # Contract v1 additions (optional for older clients).
    attempt: int = Field(default=1, ge=1)
    retry_of: str | None = None
    created_by: str | None = None


class RunStats(ContractModel):
    duration_ms: int = Field(ge=0)
    llm_calls: int = Field(ge=0)
    tokens_total: int | None = Field(ge=0)
    cost_usd: str | None = Field(default=None, pattern=r"^\d+(?:\.\d+)?$")
    skills_built: int = Field(ge=0)
    skills_reused: int = Field(ge=0)


class RecipeData(ContractModel):
    recipe: dict[str, Any]

    @field_validator("recipe")
    @classmethod
    def valid_display_recipe(cls, value: dict) -> dict:
        if not isinstance(value.get("name"), str):
            raise ValueError("The recipe must have a name.")
        if len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)) > 4096:
            raise ValueError("The recipe is too long.")
        return value


class RunStartedData(ContractModel):
    request: str = Field(min_length=1, max_length=2000)


class PlanReadyData(ContractModel):
    steps: list[str]
    skills_needed: list[str]


class SkillReusedData(ContractModel):
    skill: SkillInfo

    @field_validator("skill")
    @classmethod
    def installed(cls, value: SkillInfo) -> SkillInfo:
        if value.status != "installed":
            raise ValueError("Skill must be installed.")
        return value


class MissingSkillData(ContractModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    description: str


class CapabilityMissingData(ContractModel):
    skills: list[MissingSkillData] = Field(min_length=1)


class ForgeStartedData(ContractModel):
    skill: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    attempt: int = Field(ge=1, le=3)
    max_attempts: Literal[3]


class SkillTestsFailedData(ContractModel):
    skill: str
    attempt: int = Field(ge=1, le=3)
    tests_total: int = Field(ge=0)
    tests_failed: int = Field(ge=1)
    error_excerpt: str


class SkillCandidateReadyData(ContractModel):
    skill: SkillInfo
    attempt: int = Field(ge=1, le=3)
    tests_total: int = Field(ge=0)
    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("skill")
    @classmethod
    def candidate(cls, value: SkillInfo) -> SkillInfo:
        if value.status != "candidate" or value.origin != "agent":
            raise ValueError("Skill must be an agent-created candidate.")
        return value


class RuleDraftedData(RecipeData):
    attempt: int = Field(ge=1, le=3)
    max_attempts: Literal[3]
    explanation: str


class RuleEvaluatedData(ContractModel):
    attempt: int = Field(ge=1, le=3)
    dataset: Literal["tuning"]
    metrics: Metrics


class ValidationDoneData(ContractModel):
    dataset: Literal["validation"]
    metrics: Metrics


class SummaryData(ContractModel):
    text: str
    stats: RunStats


class VoiceReadyData(ContractModel):
    audio_url: str = Field(pattern=r"^/(?:api/)?runs/run_[a-z0-9]{4,32}/audio$")


class AwaitingApprovalData(RecipeData):
    metrics_tuning: Metrics
    metrics_validation: Metrics
    new_skills: list[SkillInfo]

    @field_validator("new_skills")
    @classmethod
    def candidates(cls, value: list[SkillInfo]) -> list[SkillInfo]:
        if any(s.status != "candidate" for s in value):
            raise ValueError("New skills must be candidates.")
        return value


class SkillInstalledData(SkillReusedData):
    pass


class RuleApprovedData(ContractModel):
    rule_name: str = Field(pattern=r"^[a-z][a-z0-9_]{2,49}$")
    comment: str | None


class RuleRejectedData(ContractModel):
    reason: str


class PolicyRejectedData(ContractModel):
    target: Literal["plan", "skill", "recipe"]
    name: str | None
    attempt: int = Field(ge=1, le=3)
    violations: list[Violation] = Field(min_length=1, max_length=10)


class RunFailedData(ContractModel):
    reason_code: ReasonCode
    reason: str


class UsageRecordData(ContractModel):
    run_id: str = Field(pattern=r"^run_[a-z0-9]{4,32}$")
    call_id: int = Field(ge=1)
    step: str
    iteration: int = Field(ge=1)
    attempt: int = Field(ge=1)
    model: str
    input_tokens: int | None = Field(ge=0)
    cached_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    cache_write_tokens: int | None = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    cost_usd: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    cost_source: Literal["provider", "pricing", "mock", "unknown"]
    currency: Literal["USD"]
    duration_ms: int = Field(ge=0)
    timestamp: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    estimated: bool
    retry: bool
    status: Literal["success", "error"]
    warning: str | None


class UsageStepData(ContractModel):
    step: str
    calls: int = Field(ge=0)
    retries: int = Field(ge=0)
    cost_usd: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    share_percent: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")


class UsageTotalsData(ContractModel):
    calls: int = Field(ge=0)
    total_tokens: int | None = Field(ge=0)
    known_tokens: int = Field(ge=0)
    cost_usd: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    known_cost_usd: str = Field(pattern=r"^\d+(?:\.\d+)?$")
    unknown_cost_calls: int = Field(ge=0)
    estimated_calls: int = Field(ge=0)
    calculated_cost_calls: int = Field(ge=0)
    retries: int = Field(ge=0)
    steps: list[UsageStepData]


class UsageSummaryData(ContractModel):
    totals: UsageTotalsData
    most_expensive_step: str | None
    average_cost_usd: str | None = Field(pattern=r"^\d+(?:\.\d+)?$")
    average_run_count: int = Field(ge=0)
    observations: list[str] = Field(min_length=1, max_length=3)


class LlmUsageData(ContractModel):
    kind: Literal["call", "summary"]
    record: UsageRecordData | None
    totals: UsageTotalsData
    summary: UsageSummaryData | None


DATA_MODELS = {
    "llm_usage": LlmUsageData,
    "run_started": RunStartedData, "plan_ready": PlanReadyData,
    "skill_reused": SkillReusedData, "capability_missing": CapabilityMissingData,
    "forge_started": ForgeStartedData, "skill_tests_failed": SkillTestsFailedData,
    "skill_candidate_ready": SkillCandidateReadyData, "rule_drafted": RuleDraftedData,
    "rule_evaluated": RuleEvaluatedData, "validation_done": ValidationDoneData,
    "summary": SummaryData, "voice_ready": VoiceReadyData,
    "awaiting_approval": AwaitingApprovalData, "skill_installed": SkillInstalledData,
    "rule_approved": RuleApprovedData, "rule_rejected": RuleRejectedData,
    "policy_rejected": PolicyRejectedData, "run_failed": RunFailedData,
}


class EventEnvelope(ContractModel):
    type: Literal["run_started", "plan_ready", "skill_reused", "capability_missing",
                  "forge_started", "skill_tests_failed", "skill_candidate_ready",
                  "rule_drafted", "rule_evaluated", "validation_done", "summary",
                  "voice_ready", "awaiting_approval", "skill_installed", "rule_approved",
                  "rule_rejected", "policy_rejected", "run_failed", "llm_usage"]
    run_id: str = Field(pattern=r"^run_[a-z0-9]{4,32}$")
    seq: int = Field(ge=1)
    timestamp: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
    phase: Phase
    message: str = Field(max_length=200)
    data: dict[str, Any]


class CreateRunBody(BaseModel):
    request: StrictStr

    @field_validator("request")
    @classmethod
    def valid_unicode(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("The request contains invalid Unicode.") from None
        return value


class ApproveBody(BaseModel):
    comment: StrictStr | None = Field(default=None, max_length=500)

    @field_validator("comment")
    @classmethod
    def valid_unicode(cls, value: str | None) -> str | None:
        return CreateRunBody.valid_unicode(value) if value is not None else None


class RejectBody(BaseModel):
    reason: StrictStr

    @field_validator("reason")
    @classmethod
    def valid_unicode(cls, value: str) -> str:
        return CreateRunBody.valid_unicode(value)
