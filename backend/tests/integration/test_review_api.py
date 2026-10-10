# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Humans can read a candidate tool's verified code before approving it."""
from __future__ import annotations

from tests.identity_support import wait_finished

REQUEST = "I want to detect directory scanning on a web server."


def test_analyst_reviews_candidate_code_before_approval(app_client, world):
    run_id = app_client.post("/runs", headers=world["alice"], json={"request": REQUEST}).json()["run_id"]
    assert wait_finished(app_client, run_id, world["alice"])["status"] == "awaiting_approval"
    events = app_client.get(f"/runs/{run_id}/events", headers=world["alice"]).json()["events"]
    ready = [e for e in events if e["type"] == "skill_candidate_ready"][-1]["data"]

    listed = app_client.get(f"/runs/{run_id}/candidates", headers=world["victor"]).json()["candidates"]
    assert [c["name"] for c in listed] == ["nginx_access_parser"]
    detail = app_client.get(f"/runs/{run_id}/candidates/nginx_access_parser", headers=world["alice"]).json()
    assert detail["code_sha256"] == ready["code_sha256"]
    assert "def run(inputs, params)" in detail["code"] and "def test_" in detail["tests"]
    assert detail["manifest"]["name"] == "nginx_access_parser" and detail["origin"] == "agent"

    # Viewers may see that candidates exist, but not read their code.
    denied = app_client.get(f"/runs/{run_id}/candidates/nginx_access_parser", headers=world["victor"])
    assert denied.status_code == 403
    # Another tenant sees nothing.
    foreign = app_client.get(f"/runs/{run_id}/candidates/nginx_access_parser", headers=world["mallory"])
    assert foreign.status_code == 404
    assert app_client.get(f"/runs/{run_id}/candidates/../../registry", headers=world["alice"]).status_code == 404

    assert app_client.post(f"/runs/{run_id}/approve", headers=world["alice"], json={}).status_code == 200
    installed = app_client.get("/skills/nginx_access_parser", headers=world["alice"]).json()
    assert installed["code_sha256"] == ready["code_sha256"] and installed["created_by_run"] == run_id
    assert app_client.get("/skills/nginx_access_parser", headers=world["mallory"]).status_code == 404
    assert app_client.get(f"/runs/{run_id}/candidates", headers=world["alice"]).json() == {"candidates": []}


def test_tampered_candidate_is_not_shown(app_client, world):
    run_id = app_client.post("/runs", headers=world["alice"], json={"request": REQUEST}).json()["run_id"]
    assert wait_finished(app_client, run_id, world["alice"])["status"] == "awaiting_approval"
    folder = app_client.app.state.gatekeepers.data_dir(world["acme"]["id"]) / "candidates" / run_id / "nginx_access_parser"
    (folder / "skill.py").write_text("def run(inputs, params):\n    return ['tampered']\n")
    response = app_client.get(f"/runs/{run_id}/candidates/nginx_access_parser", headers=world["alice"])
    assert response.status_code == 409 and response.json()["error"]["code"] == "INTEGRITY_VIOLATION"
    assert "tampered" not in response.text
