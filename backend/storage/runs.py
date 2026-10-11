# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Durable run history, event log and job queue."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult

from .db import Database
from .models import Run, RunEvent

ACTIVE = ("running", "awaiting_approval")
FINISHED = ("approved", "rejected", "failed", "cancelled")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_ms(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class RunRecord:
    id: str
    tenant_id: uuid.UUID
    created_by: str
    request: str
    status: str
    attempt: int
    retry_of: str | None
    cancel_requested: bool
    reason_code: str | None
    last_seq: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def of(cls, row: Run) -> RunRecord:
        return cls(row.id, row.tenant_id, row.created_by, row.request, row.status, row.attempt, row.retry_of,
                   row.cancel_requested, row.reason_code, row.last_seq, row.created_at, row.started_at,
                   row.finished_at)

    def info(self) -> dict:
        return {"run_id": self.id, "request": self.request, "status": self.status,
                "created_at": iso_ms(self.created_at), "finished_at": iso_ms(self.finished_at),
                "last_seq": self.last_seq, "attempt": self.attempt, "retry_of": self.retry_of,
                "created_by": self.created_by}


class RunRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def create(self, *, run_id: str, tenant_id: uuid.UUID, created_by: str, request: str,
                     status: str = "queued", attempt: int = 1, retry_of: str | None = None,
                     not_before: datetime | None = None, created_at: datetime | None = None) -> RunRecord:
        async with self.db.session() as session:
            row = Run(id=run_id, tenant_id=tenant_id, created_by=created_by, request=request, status=status,
                      attempt=attempt, retry_of=retry_of, not_before=not_before, created_at=created_at or utcnow())
            session.add(row)
            await session.flush()
            return RunRecord.of(row)

    async def record_event(self, run_id: str, event: dict, *, status: str, finished_at: datetime | None) -> None:
        """Append one event and update the run's summary row in the same transaction."""
        async with self.db.session() as session:
            session.add(RunEvent(run_id=run_id, seq=event["seq"], payload=event))
            values = {"last_seq": event["seq"], "status": status}
            if event["type"] == "run_started":
                values["started_at"] = utcnow()
            if finished_at is not None:
                values["finished_at"] = finished_at
            if event["type"] in ("run_failed", "run_cancelled"):
                values["reason_code"] = event["data"].get("reason_code")
            await session.execute(sa.update(Run).where(Run.id == run_id).values(**values))

    async def save_stats(self, run_id: str, *, llm_calls: int, tokens_total: int | None,
                         cost_usd: Decimal | None, cost_unknown: bool) -> None:
        async with self.db.session() as session:
            await session.execute(sa.update(Run).where(Run.id == run_id).values(
                llm_calls=llm_calls, tokens_total=tokens_total, cost_usd=cost_usd, cost_unknown=cost_unknown))

    async def set_status(self, run_id: str, status: str, *, reason_code: str | None = None,
                         finished: bool = False) -> None:
        async with self.db.session() as session:
            values: dict = {"status": status}
            if reason_code is not None:
                values["reason_code"] = reason_code
            if finished:
                values["finished_at"] = utcnow()
            await session.execute(sa.update(Run).where(Run.id == run_id).values(**values))

    async def cancel_queued(self, run_id: str) -> bool:
        """Cancel a run that has not started; False if the dispatcher claimed it first."""
        async with self.db.session() as session:
            result = await session.execute(sa.update(Run).where(Run.id == run_id, Run.status == "queued").values(
                status="cancelled", cancel_requested=True, finished_at=utcnow(), reason_code="CANCELLED"))
            return cast(CursorResult, result).rowcount == 1

    async def request_cancel(self, run_id: str) -> None:
        async with self.db.session() as session:
            await session.execute(sa.update(Run).where(Run.id == run_id).values(cancel_requested=True))

    async def get(self, run_id: str, tenant_id: uuid.UUID | None = None) -> RunRecord | None:
        async with self.db.session() as session:
            row = await session.get(Run, run_id)
            if row is None or (tenant_id is not None and row.tenant_id != tenant_id):
                return None
            return RunRecord.of(row)

    async def list_runs(self, tenant_id: uuid.UUID, *, limit: int = 50,
                   before: tuple[datetime, str] | None = None) -> list[RunRecord]:
        query = sa.select(Run).where(Run.tenant_id == tenant_id)
        if before is not None:
            moment, run_id = before
            query = query.where(sa.or_(Run.created_at < moment, sa.and_(Run.created_at == moment, Run.id < run_id)))
        query = query.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit)
        async with self.db.session() as session:
            return [RunRecord.of(row) for row in (await session.execute(query)).scalars()]

    async def events(self, run_id: str, after_seq: int = 0) -> list[dict]:
        async with self.db.session() as session:
            rows = await session.execute(sa.select(RunEvent.payload).where(
                RunEvent.run_id == run_id, RunEvent.seq > after_seq).order_by(RunEvent.seq))
            return [payload for payload in rows.scalars()]

    async def count(self, tenant_id: uuid.UUID, *, statuses: tuple[str, ...] | None = None,
                    since: datetime | None = None) -> int:
        query = sa.select(sa.func.count()).select_from(Run).where(Run.tenant_id == tenant_id)
        if statuses:
            query = query.where(Run.status.in_(statuses))
        if since is not None:
            query = query.where(Run.created_at >= since)
        async with self.db.session() as session:
            return (await session.execute(query)).scalar_one()

    async def count_queued(self) -> int:
        async with self.db.session() as session:
            return (await session.execute(sa.select(sa.func.count()).select_from(Run).where(Run.status == "queued"))).scalar_one()

    async def unfinished(self) -> list[RunRecord]:
        """Runs a previous process left running or awaiting approval."""
        async with self.db.session() as session:
            rows = await session.execute(sa.select(Run).where(Run.status.in_(ACTIVE)).order_by(Run.created_at))
            return [RunRecord.of(row) for row in rows.scalars()]

    async def purge_finished_before(self, cutoff: datetime) -> list[tuple[uuid.UUID, str]]:
        """Delete finished runs created before ``cutoff`` (events and usage rows cascade)."""
        async with self.db.session() as session:
            rows = (await session.execute(sa.select(Run.tenant_id, Run.id).where(
                Run.status.in_(FINISHED), Run.created_at < cutoff))).all()
            if rows:
                await session.execute(sa.delete(Run).where(Run.id.in_([run_id for _, run_id in rows])))
            return [(tenant_id, run_id) for tenant_id, run_id in rows]

    async def runs_created_by(self, actor: str, tenant_ids: list[uuid.UUID]) -> list[RunRecord]:
        if not tenant_ids:
            return []
        async with self.db.session() as session:
            rows = await session.execute(sa.select(Run).where(Run.created_by == actor, Run.tenant_id.in_(tenant_ids))
                                         .order_by(Run.created_at))
            return [RunRecord.of(row) for row in rows.scalars()]

    async def claim_next(self, now: datetime | None = None) -> RunRecord | None:
        """Take the oldest due queued run of a tenant with no active run, and mark it running.

        PostgreSQL's FOR UPDATE SKIP LOCKED lets several claimers proceed
        without taking the same run. A tenant runs one job at a time, because
        candidates of two concurrent runs could conflict at approval.
        """
        now = now or utcnow()
        busy = sa.select(Run.tenant_id).where(Run.status.in_(ACTIVE))
        query = (sa.select(Run).where(Run.status == "queued", Run.cancel_requested.is_(False),
                                      sa.or_(Run.not_before.is_(None), Run.not_before <= now),
                                      Run.tenant_id.not_in(busy))
                 .order_by(Run.created_at, Run.id).limit(1).with_for_update(skip_locked=True))
        async with self.db.session() as session:
            row = (await session.execute(query)).scalar_one_or_none()
            if row is None:
                return None
            row.status = "running"
            await session.flush()
            return RunRecord.of(row)
