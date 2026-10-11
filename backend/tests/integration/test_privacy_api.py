# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""GDPR: personal-data export and erasure, tenant deletion and retention."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orchestrator.privacy_api import purge_expired_runs
from tests.identity_support import bearer, call, wait_finished


def test_export_contains_profile_memberships_and_own_runs(app_client, world):
    run_id = app_client.post("/runs", headers=world["alice"], json={"request": "detect ssh brute force"}).json()["run_id"]
    wait_finished(app_client, run_id, world["alice"])
    data = app_client.get("/v1/me/export", headers=world["alice"]).json()
    assert data["user"]["email"] == "alice@example.test"
    assert [(m["tenant_slug"], m["role"]) for m in data["memberships"]] == [("acme", "analyst")]
    assert [r["run_id"] for r in data["runs"]] == [run_id]
    founder = app_client.get("/v1/me/export", headers=world["founder"]).json()
    assert founder["runs"] == []  # Only runs the person started.


def test_erasure_removes_personal_data_and_access(app_client, world, idp):
    alice_id = app_client.get("/me", headers=world["alice"]).json()["id"]
    assert app_client.delete("/v1/me", headers=world["alice"]).status_code == 204
    # Signing in again creates a new, empty identity: no tenant, no history.
    assert app_client.get("/me", headers=bearer(idp, "alice")).json()["error"]["code"] == "NO_TENANT"
    members = app_client.get("/members", headers=world["founder"]).json()["members"]
    assert alice_id not in {m["user_id"] for m in members}
    invitations = app_client.get("/invitations", headers=world["founder"]).json()["invitations"]
    assert "alice@example.test" not in {i["email"] for i in invitations}


def test_last_admin_cannot_erase_themselves(app_client, world):
    response = app_client.delete("/v1/me", headers=world["founder"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "LAST_ADMIN"


def test_tenant_deletion_removes_everything_and_only_that_tenant(app_client, world):
    acme_dir = app_client.app.state.gatekeepers.data_dir(world["acme"]["id"])
    run_id = app_client.post("/runs", headers=world["alice"], json={"request": "detect ssh brute force"}).json()["run_id"]
    wait_finished(app_client, run_id, world["alice"])
    globex_run = app_client.post("/runs", headers=world["mallory"], json={"request": "detect ssh brute force"}).json()["run_id"]
    assert acme_dir.exists()
    assert app_client.request("DELETE", "/v1/tenant", headers=world["alice"], json={"confirm": "acme"}).status_code == 403
    refused = app_client.request("DELETE", "/v1/tenant", headers=world["founder"], json={"confirm": "wrong"})
    assert refused.status_code == 400
    assert app_client.request("DELETE", "/v1/tenant", headers=world["founder"], json={"confirm": "acme"}).status_code == 204
    assert not acme_dir.exists()
    assert app_client.get("/me", headers=world["founder"]).json()["error"]["code"] == "NO_TENANT"
    assert globex_run in {r["run_id"] for r in app_client.get("/runs", headers=world["mallory"]).json()["runs"]}


def test_retention_deletes_only_old_finished_runs(app_client, world):
    old = app_client.post("/runs", headers=world["alice"], json={"request": "detect ssh brute force"}).json()["run_id"]
    wait_finished(app_client, old, world["alice"])
    app_client.post(f"/runs/{old}/reject", headers=world["alice"], json={"reason": "retention test"})
    usage_dir = app_client.app.state.gatekeepers.data_dir(world["acme"]["id"]) / "runs" / old
    assert usage_dir.exists()
    active = app_client.post("/runs", headers=world["alice"], json={"request": "detect ssh brute force"}).json()["run_id"]
    wait_finished(app_client, active, world["alice"])  # Awaiting approval: not finished, must survive.
    later = datetime.now(timezone.utc) + timedelta(days=400)
    assert call(app_client, purge_expired_runs, app_client.app, 365, later) == 1
    remaining = {r["run_id"] for r in app_client.get("/runs", headers=world["alice"]).json()["runs"]}
    assert remaining == {active}
    assert not usage_dir.exists()
    assert call(app_client, purge_expired_runs, app_client.app, 0, later) == 0
