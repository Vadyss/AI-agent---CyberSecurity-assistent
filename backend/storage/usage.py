# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Per-tenant model usage: one row per model call, aggregated for quotas."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

import sqlalchemy as sa

from .db import Database
from .models import LlmUsage


@dataclass(frozen=True)
class Spend:
    known_cost_usd: Decimal
    unknown_cost_calls: int
    calls: int
    tokens: int


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 else None


def _int(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


class UsageRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(self, tenant_id: uuid.UUID, run_id: str | None, record: dict) -> None:
        async with self.db.session() as session:
            session.add(LlmUsage(
                tenant_id=tenant_id, run_id=run_id, step=str(record.get("step", "unknown"))[:40],
                model=str(record.get("model", "unknown"))[:200],
                prompt_version=(str(record["prompt_version"])[:80] if record.get("prompt_version") else None),
                input_tokens=_int(record.get("input_tokens")), output_tokens=_int(record.get("output_tokens")),
                total_tokens=_int(record.get("total_tokens")), cost_usd=_decimal(record.get("cost_usd")),
                cost_source=str(record.get("cost_source", "unknown"))[:16],
                status=str(record.get("status", "unknown"))[:16]))

    async def spend(self, tenant_id: uuid.UUID, since: datetime) -> Spend:
        query = sa.select(sa.func.coalesce(sa.func.sum(LlmUsage.cost_usd), 0),
                          sa.func.count().filter(LlmUsage.cost_usd.is_(None)),
                          sa.func.count(), sa.func.coalesce(sa.func.sum(LlmUsage.total_tokens), 0)
                          ).where(LlmUsage.tenant_id == tenant_id, LlmUsage.created_at >= since)
        async with self.db.session() as session:
            known, unknown, calls, tokens = (await session.execute(query)).one()
        return Spend(Decimal(str(known)), unknown, calls, int(tokens))

    async def by_model(self, tenant_id: uuid.UUID, since: datetime) -> list[dict]:
        query = (sa.select(LlmUsage.model, sa.func.count(), sa.func.coalesce(sa.func.sum(LlmUsage.cost_usd), 0),
                           sa.func.coalesce(sa.func.sum(LlmUsage.total_tokens), 0))
                 .where(LlmUsage.tenant_id == tenant_id, LlmUsage.created_at >= since)
                 .group_by(LlmUsage.model).order_by(LlmUsage.model))
        async with self.db.session() as session:
            rows = (await session.execute(query)).all()
        return [{"model": model, "calls": calls, "known_cost_usd": format(Decimal(str(cost)).normalize(), "f"),
                 "tokens": int(tokens)} for model, calls, cost, tokens in rows]
