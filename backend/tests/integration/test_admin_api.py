# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Tenant administration and onboarding through the HTTP API."""
from __future__ import annotations

import pytest

from tests.identity_support import bearer


def test_only_admins_manage_the_tenant(app_client, world):
    for path in ("/members", "/invitations", "/api-keys"):
        assert app_client.get(path, headers=world["founder"]).status_code == 200, path
        for user in ("alice", "victor"):
            response = app_client.get(path, headers=world[user])
            assert response.status_code == 403 and response.json()["error"]["code"] == "FORBIDDEN"
    assert app_client.get("/tenant", headers=world["victor"]).json()["slug"] == "acme"


def test_invitation_flow_and_role_management(app_client, world, idp):
    created = app_client.post("/invitations", headers=world["founder"], json={"email": "Nina@Example.test", "role": "viewer"})
    assert created.status_code == 201 and created.json()["email"] == "nina@example.test"
    nina = bearer(idp, "nina")
    assert app_client.get("/me", headers=nina).json()["role"] == "viewer"
    members = {m["email"]: m for m in app_client.get("/members", headers=world["founder"]).json()["members"]}
    # Invitations turn into memberships at first login; Alice and Victor have not signed in yet.
    assert set(members) == {"founder@example.test", "nina@example.test"}
    pending = {i["email"]: i["accepted_at"] for i in app_client.get("/invitations", headers=world["founder"]).json()["invitations"]}
    assert pending["alice@example.test"] is None and pending["nina@example.test"] is not None

    nina_id = members["nina@example.test"]["user_id"]
    assert app_client.patch(f"/members/{nina_id}", headers=world["founder"], json={"role": "analyst"}).status_code == 200
    assert app_client.get("/me", headers=nina).json()["role"] == "analyst"
    assert app_client.delete(f"/members/{nina_id}", headers=world["founder"]).status_code == 204
    assert app_client.get("/me", headers=nina).json()["error"]["code"] == "NO_TENANT"

    founder_id = members["founder@example.test"]["user_id"]
    response = app_client.patch(f"/members/{founder_id}", headers=world["founder"], json={"role": "viewer"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "LAST_ADMIN"


def test_admin_cannot_touch_another_tenants_members(app_client, world):
    globex_members = app_client.get("/members", headers=world["mallory"]).json()["members"]
    mallory_id = globex_members[0]["user_id"]
    response = app_client.delete(f"/members/{mallory_id}", headers=world["founder"])
    assert response.status_code == 404
    assert len(app_client.get("/members", headers=world["mallory"]).json()["members"]) == 1


def test_api_key_secret_is_shown_once_and_works(app_client, world):
    created = app_client.post("/api-keys", headers=world["founder"],
                              json={"name": "SIEM export", "role": "viewer", "expires_in_days": 30})
    assert created.status_code == 201
    token = created.json()["key"]
    assert token.startswith("tsk_")
    listed = app_client.get("/api-keys", headers=world["founder"]).json()["api_keys"]
    assert token not in str(listed) and listed[0]["name"] == "SIEM export" and listed[0]["expires_at"]
    assert app_client.get("/runs", headers={"X-API-Key": token}).status_code == 200
    # Keys never administer, even when an admin created them.
    assert app_client.get("/api-keys", headers={"X-API-Key": token}).status_code == 403
    assert app_client.delete(f"/api-keys/{created.json()['id']}", headers=world["founder"]).status_code == 204
    assert app_client.get("/runs", headers={"X-API-Key": token}).status_code == 401


@pytest.mark.parametrize("body", [{"name": "x", "role": "admin"}, {"name": "", "role": "viewer"},
                                  {"name": "x", "role": "viewer", "expires_in_days": 0},
                                  {"name": "x", "role": "viewer", "extra": True}])
def test_invalid_api_key_requests_are_refused(app_client, world, body):
    assert app_client.post("/api-keys", headers=world["founder"], json=body).status_code == 400


def test_tenant_limits_are_admin_only_and_validated(app_client, world):
    assert app_client.patch("/tenant/limits", headers=world["alice"], json={"max_runs_per_day": 1}).status_code == 403
    assert app_client.patch("/tenant/limits", headers=world["founder"], json={"monthly_budget_usd": "-5"}).status_code == 400
    response = app_client.patch("/tenant/limits", headers=world["founder"],
                                json={"monthly_budget_usd": "12.5", "max_runs_per_day": 3})
    assert response.json() == {"monthly_budget_usd": "12.5", "max_runs_per_day": 3}
    limits = app_client.get("/tenant", headers=world["victor"]).json()["limits"]
    assert limits == {"monthly_budget_usd": "12.5", "max_runs_per_day": 3, "custom": True}


def test_self_service_onboarding(app_client, idp):
    newcomer = bearer(idp, "newcomer")
    status = app_client.get("/onboarding", headers=newcomer).json()
    assert status["memberships"] == [] and status["can_create_tenant"] is True
    created = app_client.post("/onboarding/tenants", headers=newcomer, json={"name": "Newcomer SOC"})
    assert created.status_code == 201 and created.json()["role"] == "admin"
    me = app_client.get("/me", headers=newcomer).json()
    assert me["tenant"]["slug"] == created.json()["slug"] and me["role"] == "admin"


def test_onboarding_requires_a_verified_email_and_a_login(app_client, idp):
    unverified = bearer(idp, "shady", email_verified=False)
    response = app_client.post("/onboarding/tenants", headers=unverified, json={"name": "Shady"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    assert app_client.get("/onboarding").status_code == 401
