# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from tests.unit.api_support import make_stub_app, prepare_approval


@pytest.fixture
def stub_client(tmp_path):
    app, gk = make_stub_app(tmp_path)
    with TestClient(app) as client:
        yield client


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert set(response.json()) == {"error"}
    assert set(response.json()["error"]) == {"code", "message"}
    assert response.json()["error"]["code"] == code
    assert isinstance(response.json()["error"]["message"], str)


@pytest.mark.parametrize("prefix", ["", "/api"])
def test_dual_mount_health_and_lists(stub_client, prefix):
    assert stub_client.get(prefix + "/health").json() == {"status": "ok", "contract_version": 1}
    assert stub_client.get(prefix + "/runs").json() == {"runs": []}
    assert stub_client.get(prefix + "/skills").json() == {"skills": []}


@pytest.mark.parametrize("body", [{}, [], None, {"request": None}, {"request": 7}, {"request": True},
                                   {"request": ""}, {"request": "  "}, {"request": "A" * 2001}, {"request": "\ud800"}])
def test_invalid_run_bodies_never_422(stub_client, body):
    assert_error(stub_client.post("/api/runs", content=json.dumps(body),
                                 headers={"Content-Type": "application/json"}), 400, "INVALID_REQUEST")


def test_bad_json_and_unknown_routes(stub_client):
    assert_error(stub_client.post("/api/runs", content="{", headers={"Content-Type": "application/json"}), 400, "INVALID_REQUEST")
    assert_error(stub_client.get("/api/no_such_endpoint"), 404, "INVALID_REQUEST")
    assert_error(stub_client.delete("/api/runs"), 405, "INVALID_REQUEST")


@pytest.mark.parametrize("run_id", ["run_", "RUN_ABC", "run_../x", "run_%2Fetc", "run_" + "a" * 33, "nope", "run_aabbccdd"])
@pytest.mark.parametrize("endpoint", ["events", "audio", "approve", "reject"])
def test_invalid_and_absent_ids(stub_client, run_id, endpoint):
    path = f"/api/runs/{run_id}/{endpoint}"
    response = stub_client.get(path) if endpoint in {"events", "audio"} else stub_client.post(path, json={"reason": "Ne."})
    assert_error(response, 404, "RUN_NOT_FOUND")


def test_intake_trim_unknown_fields_active_run_and_event_replay(stub_client):
    created = stub_client.post("/api/runs", json={"request": "  hello  ", "ignored": True})
    assert created.status_code == 202
    run_id = created.json()["run_id"]
    assert_error(stub_client.post("/api/runs", json={"request": "another"}), 409, "RUN_ALREADY_ACTIVE")
    run = stub_client.app.state.store.lookup(run_id)
    assert run.request == "hello"
    assert stub_client.get(f"/api/runs/{run_id}/events").json()["events"][0]["data"]["request"] == "hello"
    assert stub_client.get(f"/api/runs/{run_id}/events?after_seq=1").json() == {"events": []}
    assert_error(stub_client.get(f"/api/runs/{run_id}/audio"), 404, "AUDIO_NOT_FOUND")
    assert_error(stub_client.post(f"/api/runs/{run_id}/approve", json={}), 409, "NOT_AWAITING_APPROVAL")
    stub_client.portal.call(prepare_approval, stub_client.app, run_id)
    assert_error(stub_client.post("/api/runs", json={"request": "another"}), 409, "RUN_ALREADY_ACTIVE")


@pytest.mark.parametrize("value", ["-1", "1.5", "nan", "true", "", "abc", "١", "1e2"])
def test_bad_after_seq(stub_client, value):
    run_id = stub_client.post("/api/runs", json={"request": "hello"}).json()["run_id"]
    assert_error(stub_client.get(f"/api/runs/{run_id}/events?after_seq={value}"), 400, "INVALID_REQUEST")


@pytest.mark.parametrize("endpoint,body", [("approve", []), ("approve", {"comment": 1}),
                                         ("approve", {"comment": "x" * 501}), ("reject", {}),
                                         ("reject", {"reason": 2}), ("reject", {"reason": "  "}),
                                         ("reject", {"reason": "x" * 501}), ("reject", None)])
def test_invalid_decision_bodies(stub_client, endpoint, body):
    run_id = stub_client.post("/api/runs", json={"request": "hello"}).json()["run_id"]
    assert_error(stub_client.post(f"/api/runs/{run_id}/{endpoint}", json=body), 400, "INVALID_REQUEST")


@pytest.mark.parametrize("body", [None, {}, {"comment": None}, {"comment": ""}])
def test_optional_approval_body(stub_client, body):
    run_id = stub_client.post("/api/runs", json={"request": "hello"}).json()["run_id"]
    stub_client.portal.call(prepare_approval, stub_client.app, run_id)
    kwargs = {} if body is None else {"json": body}
    assert stub_client.post(f"/api/runs/{run_id}/approve", **kwargs).json() == {"status": "approved"}
    assert_error(stub_client.post(f"/api/runs/{run_id}/approve", json={}), 409, "NOT_AWAITING_APPROVAL")
    second = stub_client.post("/api/runs", json={"request": "second"}).json()["run_id"]
    assert [r["run_id"] for r in stub_client.get("/api/runs").json()["runs"]] == [second, run_id]
