# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Authentication, roles and tenant isolation through the real HTTP and WebSocket API."""
from __future__ import annotations

import json
import time

import pytest
from starlette.websockets import WebSocketDisconnect

from identity.service import IdentityService
from tests.identity_support import ISSUER, bearer, call, unsigned_token, wait_finished
from tests.integration.conftest import FOUNDER

@pytest.mark.parametrize("method,path", [("get", "/me"), ("get", "/runs"), ("post", "/runs"), ("get", "/skills"),
                                         ("get", "/runs/run_abcd1234/events"), ("post", "/runs/run_abcd1234/approve"),
                                         ("get", "/api/runs")])
def test_every_endpoint_except_health_requires_authentication(app_client, method, path):
    response = app_client.request(method.upper(), path, json={"request": "x"} if method == "post" else None)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert response.headers["WWW-Authenticate"].startswith("Bearer")
    assert app_client.get("/health").status_code == 200


def test_identity_headers_without_a_signed_token_are_ignored(app_client):
    forged = {"X-Forwarded-User": "founder", "X-Forwarded-Email": FOUNDER, "X-Auth-Request-Email": FOUNDER,
              "X-Forwarded-Preferred-Username": "founder"}
    assert app_client.get("/me", headers=forged).status_code == 401


@pytest.mark.parametrize("token", ["not-a-token", unsigned_token(sub="founder", email=FOUNDER, email_verified=True)])
def test_forged_tokens_are_refused(app_client, token):
    response = app_client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401 and response.json()["error"]["code"] == "INVALID_TOKEN"


def test_token_for_another_client_is_refused(app_client, idp):
    response = app_client.get("/me", headers=bearer(idp, "founder", FOUNDER, aud="some-other-app"))
    assert response.status_code == 401


def test_bootstrap_admin_and_user_without_tenant(app_client, idp):
    me = app_client.get("/me", headers=bearer(idp, "founder", FOUNDER)).json()
    assert (me["role"], me["tenant"]["slug"], "manage" in me["permissions"]) == ("admin", "acme", True)
    response = app_client.get("/me", headers=bearer(idp, "stranger"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "NO_TENANT"


def test_roles_limit_actions(app_client, world):
    assert app_client.get("/me", headers=world["victor"]).json()["role"] == "viewer"
    assert app_client.get("/runs", headers=world["victor"]).status_code == 200
    denied = app_client.post("/runs", headers=world["victor"], json={"request": "detect ssh brute force"})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "FORBIDDEN"
    assert app_client.post("/runs/run_abcd1234/approve", headers=world["victor"], json={}).status_code == 403


def test_tenants_cannot_see_or_decide_each_others_runs(app_client, world):
    created = app_client.post("/runs", headers=world["alice"], json={"request": "detect ssh brute force"})
    assert created.status_code == 202
    run_id = created.json()["run_id"]
    assert wait_finished(app_client, run_id, world["alice"])["status"] == "awaiting_approval"

    assert app_client.get("/runs", headers=world["mallory"]).json() == {"runs": []}
    for method, path in (("get", f"/runs/{run_id}/events"), ("post", f"/runs/{run_id}/approve"),
                         ("post", f"/runs/{run_id}/reject"), ("get", f"/runs/{run_id}/audio")):
        response = app_client.request(method.upper(), path, headers=world["mallory"],
                                      json={"reason": "no"} if method == "post" else None)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "RUN_NOT_FOUND"
    # Asking for the foreign tenant explicitly is refused without revealing it exists.
    response = app_client.get("/runs", headers={**world["mallory"], "X-Toolsmith-Tenant": world["acme"]["slug"]})
    assert response.status_code == 403 and response.json()["error"]["code"] == "TENANT_FORBIDDEN"


def test_approved_tools_stay_in_their_tenant_and_audit_names_the_approver(app_client, world):
    created = app_client.post("/runs", headers=world["alice"],
                              json={"request": "I want to detect directory scanning on a web server."})
    run_id = created.json()["run_id"]
    assert wait_finished(app_client, run_id, world["alice"])["status"] == "awaiting_approval"
    assert app_client.post(f"/runs/{run_id}/approve", headers=world["alice"], json={}).status_code == 200

    acme_skills = {s["name"] for s in app_client.get("/skills", headers=world["alice"]).json()["skills"]}
    globex_skills = {s["name"] for s in app_client.get("/skills", headers=world["mallory"]).json()["skills"]}
    assert "nginx_access_parser" in acme_skills and "nginx_access_parser" not in globex_skills

    alice_id = app_client.get("/me", headers=world["alice"]).json()["id"]
    pool = app_client.app.state.gatekeepers
    audit = pool.data_dir(world["acme"]["id"]) / "audit.jsonl"
    records = [json.loads(line) for line in audit.read_text().splitlines()]
    approved = [r for r in records if r["kind"] == "rule_approved"]
    assert approved and approved[-1]["actor"] == f"user:{alice_id}"
    assert all(r["actor"] == f"user:{alice_id}" for r in records if r["run_id"] == run_id)
    assert not (pool.data_dir(world["globex"]["id"]) / "registry" / "nginx_access_parser").exists()


def test_websocket_requires_authentication_and_only_streams_own_tenant(app_client, world):
    with pytest.raises(WebSocketDisconnect) as closed:
        with app_client.websocket_connect("/ws") as ws:
            ws.receive_json()
    assert closed.value.code == 1008
    with app_client.websocket_connect("/ws", headers=world["mallory"]) as foreign, \
            app_client.websocket_connect("/ws", headers=world["alice"]) as own:
        run_id = app_client.post("/runs", headers=world["alice"], json={"request": "detect ssh brute force"}).json()["run_id"]
        first = own.receive_json()
        assert (first["run_id"], first["type"]) == (run_id, "run_started")
        wait_finished(app_client, run_id, world["alice"])
        # Prove the foreign socket is alive yet received nothing of tenant Acme's run.
        mallory_run = app_client.post("/runs", headers=world["mallory"], json={"request": "detect ssh brute force"}).json()["run_id"]
        assert foreign.receive_json()["run_id"] == mallory_run


def test_api_keys_authenticate_with_their_role_and_stop_when_revoked(app_client, world):
    import uuid
    service: IdentityService = app_client.app.state.identity
    acme_id = uuid.UUID(world["acme"]["id"])
    analyst_key, analyst_token = call(app_client, service.create_api_key, acme_id, "ci", "analyst", None)
    _, viewer_token = call(app_client, service.create_api_key, acme_id, "dashboard", "viewer", None)
    me = app_client.get("/me", headers={"X-API-Key": analyst_token}).json()
    assert (me["kind"], me["role"], me["tenant"]["slug"]) == ("api_key", "analyst", "acme")
    assert app_client.post("/runs", headers={"X-API-Key": viewer_token}, json={"request": "x"}).status_code == 403
    assert app_client.get("/runs", headers={"X-API-Key": viewer_token}).status_code == 200
    call(app_client, service.revoke_api_key, acme_id, analyst_key.id)
    response = app_client.get("/runs", headers={"X-API-Key": analyst_token})
    assert response.status_code == 401 and response.json()["error"]["code"] == "INVALID_API_KEY"
