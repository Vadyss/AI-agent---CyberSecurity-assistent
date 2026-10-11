# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Response models of the versioned API; they define the published OpenAPI contract."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from gatekeeper.types import SkillInfo
from orchestrator.models import EventEnvelope, RunInfo, Status


class ErrorDetail(BaseModel):
    code: str = Field(description="Stable machine-readable code, for example RUN_NOT_FOUND.")
    message: str = Field(description="Human-readable English explanation, safe to show to users.")


class ErrorResponse(BaseModel):
    error: ErrorDetail


class HealthResponse(BaseModel):
    status: Literal["ok"]
    contract_version: int


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, bool]


class TenantRef(BaseModel):
    id: str
    slug: str


class MeResponse(BaseModel):
    kind: Literal["user", "api_key", "local"]
    id: str
    email: str | None
    role: Literal["admin", "analyst", "viewer"]
    tenant: TenantRef
    permissions: list[str]


class RunCreated(BaseModel):
    run_id: str
    status: Literal["running", "queued"]


class RunsResponse(BaseModel):
    runs: list[RunInfo]


class EventsResponse(BaseModel):
    events: list[EventEnvelope]


class DecisionResponse(BaseModel):
    status: Literal["approved", "rejected"]


class RunStatusResponse(BaseModel):
    status: Status


class SkillsResponse(BaseModel):
    skills: list[SkillInfo]


class CandidatesResponse(BaseModel):
    candidates: list[SkillInfo]


class ToolReview(BaseModel):
    name: str
    version: int
    kind: str
    origin: Literal["seed", "agent"]
    manifest: dict[str, Any]
    code: str
    tests: str
    code_sha256: str
    tests_sha256: str
    created_by_run: str | None
    created_at: str


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorResponse, "description": description}
    for status, description in {
        400: "Invalid request.",
        401: "Not signed in, or the token or API key is invalid.",
        403: "Signed in, but the role or tenant does not allow this.",
        404: "Not found in your tenant.",
        409: "The run is not in a state that allows this.",
        429: "Rate limit, queue limit or quota reached; see Retry-After when present.",
    }.items()
}
