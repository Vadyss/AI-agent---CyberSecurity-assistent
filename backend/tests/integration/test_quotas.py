# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Per-tenant usage accounting and quota enforcement."""
from __future__ import annotations

import time
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from identity.service import LOCAL_TENANT_ID
from orchestrator.llm import TenantBudget, TenantBudgetExceeded, TenantSpend, count_call, count_cost
from orchestrator.main import create_app
from orchestrator.quotas import QuotaService
from tests.fakes import InProcessSandbox, test_settings
from tests.identity_support import call


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(settings=test_settings(data_dir=tmp_path / "data"), sandbox=InProcessSandbox())) as client:
        yield client


def finish(client, run_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = {r["run_id"]: r for r in client.get("/runs").json()["runs"]}[run_id]
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.02)
    raise AssertionError("The run did not finish.")


def set_limits(client, budget=None, runs=None):
    call(client, client.app.state.identity.update_limits, LOCAL_TENANT_ID,
         monthly_budget_usd=budget, max_runs_per_day=runs)


def test_every_model_call_is_recorded_for_the_tenant(client):
    run_id = client.post("/runs", json={"request": "detect ssh brute force"}).json()["run_id"]
    assert finish(client, run_id)["status"] == "awaiting_approval"
    report = client.get("/usage").json()
    events = client.get(f"/runs/{run_id}/events").json()["events"]
    model_calls = [e for e in events if e["type"] == "llm_usage" and e["data"]["kind"] == "call"]
    assert report["calls"] == len(model_calls) > 0
    assert report["known_cost_usd"] == "0" and report["runs_today"] == 1
    assert report["by_model"][0]["model"] == "mock"


def test_daily_run_limit(client):
    set_limits(client, runs=1)
    assert client.post("/runs", json={"request": "detect ssh brute force"}).status_code == 202
    refused = client.post("/runs", json={"request": "detect ssh brute force"})
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "QUOTA_EXCEEDED"
    assert "daily run limit" in refused.json()["error"]["message"]


def test_exhausted_monthly_budget_blocks_new_runs(client):
    set_limits(client, budget=Decimal("0"))
    refused = client.post("/runs", json={"request": "detect ssh brute force"})
    assert refused.status_code == 429 and "budget" in refused.json()["error"]["message"]
    usage = client.get("/usage").json()
    assert (usage["monthly_budget_usd"], usage["remaining_usd"]) == ("0", "0")


def test_budget_running_out_mid_run_stops_it_without_retry(client, monkeypatch):
    async def nothing_left(self, tenant_id):
        return Decimal("0")
    async def allowed(self, tenant_id):
        return Decimal("1")
    monkeypatch.setattr(QuotaService, "remaining_budget", nothing_left)
    monkeypatch.setattr(QuotaService, "check_can_start", allowed)
    run_id = client.post("/runs", json={"request": "detect ssh brute force"}).json()["run_id"]
    run = finish(client, run_id)
    last = client.get(f"/runs/{run_id}/events").json()["events"][-1]
    assert (run["status"], last["data"]["reason_code"]) == ("failed", "QUOTA_EXCEEDED")
    time.sleep(0.2)
    assert len(client.get("/runs").json()["runs"]) == 1  # Quota failures are not retried.


def test_budget_guard_counts_down_with_known_costs():
    token = TenantSpend.set(TenantBudget(Decimal("0.010")))
    try:
        count_call(25)
        count_cost(Decimal("0.004"))
        count_cost(None)  # Unknown cost cannot be charged.
        count_call(25)
        count_cost(Decimal("0.006"))
        with pytest.raises(TenantBudgetExceeded):
            count_call(25)
    finally:
        TenantSpend.reset(token)


def test_every_recorded_call_names_its_prompt_version(client):
    import sqlalchemy as sa
    from orchestrator.llm import prompt_versions
    from storage.models import LlmUsage

    run_id = client.post("/runs", json={"request": "detect ssh brute force"}).json()["run_id"]
    finish(client, run_id)
    events = client.get(f"/runs/{run_id}/events").json()["events"]
    records = [e["data"]["record"] for e in events if e["type"] == "llm_usage" and e["data"]["kind"] == "call"]
    assert {r["prompt_version"] for r in records} <= set(prompt_versions().values())

    async def stored():
        async with client.app.state.db.session() as session:
            return set((await session.execute(sa.select(LlmUsage.prompt_version))).scalars())
    assert call(client, stored) == {r["prompt_version"] for r in records}
    assert client.get("/v1/version").json()["prompts"] == prompt_versions()
