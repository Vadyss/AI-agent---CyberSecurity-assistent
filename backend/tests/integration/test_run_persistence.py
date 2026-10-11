# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Runs and events are durable in PostgreSQL and survive a backend restart."""
from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from orchestrator.main import RESTART_REASON, create_app
from storage.models import Tenant
from storage.runs import RunRepository
from tests.conftest import _truncate
from tests.fakes import InProcessSandbox, make_settings


@pytest.fixture
async def clean_postgres(postgres_url):
    from storage.db import Database
    db = Database(postgres_url, pool_size=1)
    await _truncate(db)
    yield postgres_url
    await _truncate(db)
    await db.close()


def app_for(url, tmp_path, **kwargs):
    settings = make_settings(data_dir=tmp_path / "data", database_url=url)
    return create_app(settings=settings, sandbox=InProcessSandbox(), **kwargs)


def finish(client, run_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = {r["run_id"]: r["status"] for r in client.get("/runs").json()["runs"]}[run_id]
        if status not in {"queued", "running"}:
            return status
        time.sleep(0.02)
    raise AssertionError("The run did not finish.")


def test_history_and_events_survive_a_restart(clean_postgres, tmp_path):
    with TestClient(app_for(clean_postgres, tmp_path)) as first:
        run_id = first.post("/runs", json={"request": "detect ssh brute force"}).json()["run_id"]
        assert finish(first, run_id) == "awaiting_approval"
        assert first.post(f"/runs/{run_id}/approve", json={}).status_code == 200
        events = first.get(f"/runs/{run_id}/events").json()["events"]
    with TestClient(app_for(clean_postgres, tmp_path)) as second:
        runs = second.get("/runs").json()["runs"]
        assert [(r["run_id"], r["status"]) for r in runs] == [(run_id, "approved")]
        assert runs[0]["created_by"] == "local:local" and runs[0]["finished_at"]
        assert second.get(f"/runs/{run_id}/events").json()["events"] == events
        assert second.get(f"/runs/{run_id}/events?after_seq={len(events) - 1}").json()["events"] == events[-1:]
        # A finished run from the previous process cannot be decided again.
        assert second.post(f"/runs/{run_id}/approve", json={}).json()["error"]["code"] == "NOT_AWAITING_APPROVAL"


async def held(run, gk, roles, emitter, settings, voice):
    await emitter.emit(run, "run_started", "intake", {"request": run.request})
    await asyncio.Event().wait()


def test_run_interrupted_by_a_restart_is_closed_as_failed(clean_postgres, tmp_path):
    with TestClient(app_for(clean_postgres, tmp_path, pipeline_runner=held)) as first:
        run_id = first.post("/runs", json={"request": "never finishes"}).json()["run_id"]
        time.sleep(0.1)
    with TestClient(app_for(clean_postgres, tmp_path)) as second:
        assert {r["run_id"]: r["status"] for r in second.get("/runs").json()["runs"]}[run_id] == "failed"
        last = second.get(f"/runs/{run_id}/events").json()["events"][-1]
        assert (last["type"], last["data"]["reason"]) == ("run_failed", RESTART_REASON)
        # The interrupted run is retried automatically as a new run.
        retries = [r for r in second.get("/runs").json()["runs"] if r["retry_of"] == run_id]
        assert len(retries) == 1 and retries[0]["attempt"] == 2


async def test_database_allows_one_active_run_per_tenant(database):
    async with database.session() as session:
        tenant = Tenant(slug="acme", name="Acme")
        session.add(tenant)
    repository = RunRepository(database)
    await repository.create(run_id="run_aaaa000000000001", tenant_id=tenant.id, created_by="x", request="r", status="running")
    await repository.create(run_id="run_aaaa000000000002", tenant_id=tenant.id, created_by="x", request="r", status="queued")
    with pytest.raises(IntegrityError):
        await repository.create(run_id="run_aaaa000000000003", tenant_id=tenant.id, created_by="x", request="r",
                                status="awaiting_approval")


async def test_foreign_tenant_cannot_read_a_stored_run(database):
    async with database.session() as session:
        one, two = Tenant(slug="one", name="One"), Tenant(slug="two", name="Two")
        session.add_all([one, two])
    repository = RunRepository(database)
    await repository.create(run_id="run_bbbb000000000001", tenant_id=one.id, created_by="x", request="r")
    assert await repository.get("run_bbbb000000000001", one.id) is not None
    assert await repository.get("run_bbbb000000000001", two.id) is None
    assert await repository.get("run_bbbb000000000001", uuid.uuid4()) is None
    assert await repository.list_runs(two.id) == []
