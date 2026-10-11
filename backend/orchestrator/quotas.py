# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Per-tenant quotas: runs per day and a monthly model budget."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from orchestrator.api_errors import ApiError


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class Limits:
    monthly_budget_usd: Decimal
    max_runs_per_day: int


class QuotaService:
    def __init__(self, settings, identity, runs, usage, clock=lambda: datetime.now(timezone.utc)) -> None:
        self.settings, self.identity, self.runs, self.usage, self.clock = settings, identity, runs, usage, clock

    async def limits(self, tenant_id: uuid.UUID) -> Limits:
        tenant = await self.identity.get_tenant(tenant_id)
        budget = tenant.monthly_budget_usd
        runs = tenant.max_runs_per_day
        return Limits(Decimal(self.settings.default_monthly_budget_usd) if budget is None else budget,
                      self.settings.default_max_runs_per_day if runs is None else runs)

    async def remaining_budget(self, tenant_id: uuid.UUID) -> Decimal:
        limits = await self.limits(tenant_id)
        spend = await self.usage.spend(tenant_id, month_start(self.clock()))
        return limits.monthly_budget_usd - spend.known_cost_usd

    async def check_can_start(self, tenant_id: uuid.UUID) -> Decimal:
        """Refuse a new run that would break a quota; returns the remaining budget."""
        limits = await self.limits(tenant_id)
        today = await self.runs.count(tenant_id, since=day_start(self.clock()))
        if today >= limits.max_runs_per_day:
            raise ApiError(429, "QUOTA_EXCEEDED", "The tenant has reached its daily run limit.")
        remaining = await self.remaining_budget(tenant_id)
        if remaining <= 0:
            raise ApiError(429, "QUOTA_EXCEEDED", "The tenant's monthly model budget is used up. "
                                                  "An administrator can raise it.")
        return remaining

    async def report(self, tenant_id: uuid.UUID) -> dict:
        now = self.clock()
        limits = await self.limits(tenant_id)
        spend = await self.usage.spend(tenant_id, month_start(now))
        return {"period_start": month_start(now).isoformat().replace("+00:00", "Z"),
                "calls": spend.calls, "tokens": spend.tokens,
                "known_cost_usd": format(spend.known_cost_usd.normalize(), "f"),
                "unknown_cost_calls": spend.unknown_cost_calls,
                "monthly_budget_usd": format(limits.monthly_budget_usd.normalize(), "f"),
                "remaining_usd": format(max(Decimal(0), limits.monthly_budget_usd - spend.known_cost_usd).normalize(), "f"),
                "runs_today": await self.runs.count(tenant_id, since=day_start(now)),
                "max_runs_per_day": limits.max_runs_per_day,
                "by_model": await self.usage.by_model(tenant_id, month_start(now))}
