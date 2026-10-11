# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Durable queue: ordering, per-tenant exclusivity, retries and cancellation."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from gatekeeper.types import SandboxError
from orchestrator import main as main_module
from orchestrator.main import create_app
from storage.models import Tenant
from storage.runs import RunRepository
from tests.fakes import InProcessSandbox, tenant_data_dir, make_settings


class FlakySandbox(InProcessSandbox):
    def __init__(self, failures: int):
        self.failures = failures

    async def execute(self, job):
        if self.failures > 0:
            self.failures -= 1
            raise SandboxError("The sandbox is unavailable.")
        return await super().execute(job)


async def held(run, gk, roles, emitter, settings, voice):
    await emitter.emit(run, "run_started", "intake", {"request": run.request})
    await asyncio.Event().wait()


def client_for(tmp_path, **kwargs):
    overrides = {key: kwargs.pop(key) for key in list(kwargs) if key in {"max_queued_runs_per_tenant", "run_max_attempts"}}
    sandbox = kwargs.pop("sandbox", None) or InProcessSandbox()
    return TestClient(create_app(settings=make_settings(data_dir=tmp_path / "data", **overrides), sandbox=sandbox, **kwargs))


def runs(client):
    return {r["run_id"]: r for r in client.get("/runs").json()["runs"]}


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("Condition not reached.")


def test_queued_run_starts_when_the_tenants_active_run_finishes(tmp_path):
    with client_for(tmp_path) as client:
        first = client.post("/runs", json={"request": "detect ssh brute force"}).json()
        second = client.post("/runs", json={"request": "detect ssh brute force"}).json()
        assert (first["status"], second["status"]) == ("running", "queued")
        wait_for(lambda: runs(client)[first["run_id"]]["status"] == "awaiting_approval")
        assert runs(client)[second["run_id"]]["status"] == "queued"  # Awaiting approval keeps the tenant busy.
        assert client.post(f"/runs/{first['run_id']}/reject", json={"reason": "Not now."}).status_code == 200
        wait_for(lambda: runs(client)[second["run_id"]]["status"] == "awaiting_approval")


def test_queue_is_bounded_per_tenant(tmp_path):
    with client_for(tmp_path, pipeline_runner=held, max_queued_runs_per_tenant=2) as client:
        client.post("/runs", json={"request": "one"})
        client.post("/runs", json={"request": "two"})
        client.post("/runs", json={"request": "three"})
        response = client.post("/runs", json={"request": "four"})
        assert response.status_code == 429 and response.json()["error"]["code"] == "QUEUE_FULL"


def test_cancel_queued_running_and_awaiting_runs(tmp_path):
    with client_for(tmp_path, pipeline_runner=held) as client:
        running = client.post("/runs", json={"request": "one"}).json()["run_id"]
        queued = client.post("/runs", json={"request": "two"}).json()["run_id"]
        assert client.post(f"/runs/{queued}/cancel").json() == {"status": "cancelled"}
        assert client.get(f"/runs/{queued}/events").json() == {"events": []}
        assert client.post(f"/runs/{running}/cancel").json() == {"status": "cancelled"}
        events = client.get(f"/runs/{running}/events").json()["events"]
        assert [e["type"] for e in events][-1] == "run_cancelled"
        assert runs(client)[running]["status"] == "cancelled" and runs(client)[running]["finished_at"]
        conflict = client.post(f"/runs/{running}/cancel")
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "NOT_CANCELLABLE"


def test_cancelling_a_real_run_discards_its_candidates(tmp_path):
    with client_for(tmp_path) as client:
        run_id = client.post("/runs", json={"request": "I want to detect directory scanning on a web server."}).json()["run_id"]
        wait_for(lambda: any(e["type"] == "forge_started" for e in client.get(f"/runs/{run_id}/events").json()["events"]))
        response = client.post(f"/runs/{run_id}/cancel")
        if response.status_code == 409:  # The mock finished first; nothing left to cancel.
            pytest.skip("The run finished before it could be cancelled.")
        assert response.json() == {"status": "cancelled"}
        assert not (tenant_data_dir(client.app) / "candidates" / run_id).exists()
        names = {s["name"] for s in client.get("/skills").json()["skills"]}
        assert "nginx_access_parser" not in names
        assert '"kind":"run_cancelled"' in (tenant_data_dir(client.app) / "audit.jsonl").read_text()


def test_transient_failure_is_retried_once_as_a_new_run(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "RETRY_BACKOFF_S", 0)
    with client_for(tmp_path, sandbox=FlakySandbox(failures=1)) as client:
        first = client.post("/runs", json={"request": "detect ssh brute force"}).json()["run_id"]
        retry = wait_for(lambda: next((r for r in runs(client).values() if r["retry_of"] == first), None))
        assert runs(client)[first]["status"] == "failed" and retry["attempt"] == 2
        wait_for(lambda: runs(client)[retry["run_id"]]["status"] == "awaiting_approval")


def test_retries_stop_at_the_attempt_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "RETRY_BACKOFF_S", 0)
    with client_for(tmp_path, sandbox=FlakySandbox(failures=100), run_max_attempts=2) as client:
        first = client.post("/runs", json={"request": "detect ssh brute force"}).json()["run_id"]
        retry = wait_for(lambda: next((r for r in runs(client).values() if r["retry_of"] == first), None))
        wait_for(lambda: runs(client)[retry["run_id"]]["status"] == "failed")
        time.sleep(0.3)
        assert len(runs(client)) == 2


def test_permanent_failure_is_not_retried_but_can_be_retried_by_hand(tmp_path):
    with client_for(tmp_path) as client:
        failed = client.post("/runs", json={"request": "Demonstrate a forge failure."}).json()["run_id"]
        wait_for(lambda: runs(client)[failed]["status"] == "failed")
        time.sleep(0.2)
        assert len(runs(client)) == 1
        manual = client.post(f"/runs/{failed}/retry")
        assert manual.status_code == 202
        record = runs(client)[manual.json()["run_id"]]
        assert (record["retry_of"], record["attempt"]) == (failed, 2)
        assert client.post(f"/runs/{manual.json()['run_id']}/retry").status_code in {409, 202}


async def test_claim_order_and_one_active_run_per_tenant(database):
    async with database.session() as session:
        one, two = Tenant(slug="one", name="One"), Tenant(slug="two", name="Two")
        session.add_all([one, two])
    repository = RunRepository(database)
    start = datetime.now(timezone.utc)
    for n, tenant in enumerate((one, one, two)):
        await repository.create(run_id=f"run_cccc00000000000{n}", tenant_id=tenant.id, created_by="x", request="r",
                                created_at=start + timedelta(seconds=n))
    await repository.create(run_id="run_cccc000000000009", tenant_id=two.id, created_by="x", request="later",
                            not_before=start + timedelta(hours=1), created_at=start - timedelta(seconds=5))
    first = await repository.claim_next()
    second = await repository.claim_next()
    assert (first.id, second.id) == ("run_cccc000000000000", "run_cccc000000000002")
    assert await repository.claim_next() is None  # Both tenants busy; the delayed run is not due.
    await repository.set_status(first.id, "failed", finished=True)
    assert (await repository.claim_next()).id == "run_cccc000000000001"
    assert await repository.cancel_queued("run_cccc000000000009") is True
    assert await repository.cancel_queued("run_cccc000000000001") is False  # Already running.
