# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Bounded, process-local run history and atomic intake."""
from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from orchestrator.api_errors import ApiError
from orchestrator.models import RunInfo, RunStats

PHASES = ("intake", "plan", "forge", "rule", "validation", "approval", "done")
ACTIVE_STATUSES = {"running", "awaiting_approval"}
FINISHED_STATUSES = {"approved", "rejected", "failed", "cancelled"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class RunCounters:
    llm_calls: int = 0
    tokens_total: int | None = None
    cost_usd: Decimal | None = None
    cost_unknown: bool = False
    skills_built: int = 0
    skills_reused: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)

    def snapshot(self) -> dict:
        return RunStats(duration_ms=max(0, int((time.monotonic() - self.started_monotonic) * 1000)),
                        llm_calls=self.llm_calls, tokens_total=self.tokens_total,
                        cost_usd=None if self.cost_unknown or self.cost_usd is None else format(Decimal(str(self.cost_usd)), "f"),
                        skills_built=self.skills_built, skills_reused=self.skills_reused).model_dump(mode="json")


@dataclass
class PendingApproval:
    recipe: dict
    metrics_tuning: Any
    metrics_validation: Any
    new_skills: list
    attack_type: str


@dataclass
class RunState:
    run_id: str
    request: str
    tenant_id: uuid.UUID | None = None
    created_by: str = "system"
    attempt: int = 1
    # Set by the pipeline for transient failures (sandbox, model provider outage).
    retryable: bool = False
    cancel_requested: bool = False
    status: str = "running"
    created_at: str = field(default_factory=now_iso)
    finished_at: str | None = None
    events: list[dict] = field(default_factory=list)
    last_seq: int = 0
    phase_index: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    decision_taken: bool = False
    pending: PendingApproval | None = None
    stats: RunCounters = field(default_factory=RunCounters)
    task: asyncio.Task | None = None
    voice_task: asyncio.Task | None = None
    audio: bytes | None = None

    @property
    def phase(self) -> str:
        return PHASES[self.phase_index]

    def info(self) -> dict:
        return RunInfo(run_id=self.run_id, request=self.request, status=self.status,
                       created_at=self.created_at, finished_at=self.finished_at,
                       last_seq=self.last_seq).model_dump(mode="json")


class RunStore:
    def __init__(self, max_runs: int = 50):
        self.runs: dict[str, RunState] = {}
        self.lock = asyncio.Lock()
        self.max_runs = max_runs

    def adopt(self, run_id: str, request: str, tenant_id: uuid.UUID, created_by: str, attempt: int = 1) -> RunState:
        """Materialize a run the queue just claimed; the pipeline then emits run_started."""
        self._evict()
        run = RunState(run_id, request, tenant_id, created_by, attempt=attempt)
        self.runs[run_id] = run
        return run

    def _evict(self) -> None:
        # Finished runs stay in the database; memory only keeps recent ones.
        while len(self.runs) >= self.max_runs:
            oldest = next((r for r in self.runs.values() if r.status in FINISHED_STATUSES), None)
            if oldest is None:
                return
            if oldest.voice_task and not oldest.voice_task.done():
                oldest.voice_task.cancel()
            del self.runs[oldest.run_id]

    def lookup(self, run_id: str) -> RunState:
        """Internal access without a tenant check; API handlers must use ``get``."""
        if not re.fullmatch(r"run_[a-z0-9]{4,32}", run_id) or run_id not in self.runs:
            raise ApiError(404, "RUN_NOT_FOUND", "Run not found.")
        return self.runs[run_id]

    def find(self, run_id: str, tenant_id: uuid.UUID | None) -> RunState | None:
        """The live run of this tenant, or None (finished runs may live only in the database)."""
        run = self.runs.get(run_id)
        return run if run is not None and run.tenant_id == tenant_id else None

    def get(self, run_id: str, tenant_id: uuid.UUID | None) -> RunState:
        run = self.lookup(run_id)
        # Another tenant's run is reported exactly like a missing one.
        if run.tenant_id != tenant_id:
            raise ApiError(404, "RUN_NOT_FOUND", "Run not found.")
        return run

    def list(self, tenant_id: uuid.UUID | None) -> list[dict]:
        return [r.info() for r in reversed(list(self.runs.values())) if r.tenant_id == tenant_id]

    async def close(self) -> None:
        tasks = [t for r in self.runs.values() for t in (r.task, r.voice_task) if t and not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
