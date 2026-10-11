# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from orchestrator.main import create_app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS",
        " http://localhost:3000, http://127.0.0.1:3000 , https://frontend.example, ",
    )
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("AUTH_MODE", "disabled")
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:3000", "http://127.0.0.1:3000", "https://frontend.example"],
)
def test_allowed_origin_can_read_health(client, origin):
    response = client.get("/health", headers={"Origin": origin})

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "contract_version": 1}
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert "Access-Control-Allow-Credentials" not in response.headers


def test_unknown_origin_cannot_read_response_in_browser(client):
    response = client.get("/health", headers={"Origin": "https://unknown.example"})

    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize("origin", ["http://localhost:3000", "https://unknown.example"])
def test_unexpected_error_keeps_cors_policy(client, origin):
    @client.app.get("/v1/test-unexpected-error")  # Every public path is served from /v1.
    async def unexpected_error():
        raise RuntimeError("Test failure")

    response = client.get("/test-unexpected-error", headers={"Origin": origin})

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "Unexpected backend error."}
    }
    assert response.headers["Vary"] == "Origin"
    assert "Access-Control-Allow-Credentials" not in response.headers
    if origin == "http://localhost:3000":
        assert response.headers["Access-Control-Allow-Origin"] == origin
    else:
        assert "Access-Control-Allow-Origin" not in response.headers


def test_json_post_preflight_and_create_run(client):
    origin = "http://localhost:3000"
    preflight = client.options(
        "/runs",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["Access-Control-Allow-Origin"] == origin
    assert set(preflight.headers["Access-Control-Allow-Methods"].split(", ")) == {
        "GET", "POST"
    }
    assert "content-type" in preflight.headers["Access-Control-Allow-Headers"].lower()
    assert "Access-Control-Allow-Credentials" not in preflight.headers

    response = client.post("/runs", headers={"Origin": origin}, json={"request": "Test"})

    assert response.status_code == 202
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.json()["status"] == "running"


@pytest.mark.parametrize(
    "origin,method,headers",
    [
        ("https://unknown.example", "POST", "content-type"),
        ("http://localhost:3000", "DELETE", "content-type"),
        ("http://localhost:3000", "POST", "x-unapproved-header"),
    ],
)
def test_disallowed_preflight_is_rejected(client, origin, method, headers):
    response = client.options(
        "/runs",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )

    assert response.status_code == 400
    if origin == "https://unknown.example":
        assert "Access-Control-Allow-Origin" not in response.headers


@pytest.mark.parametrize(
    "origin", ["http://localhost:3000", "https://frontend.example", None]
)
def test_websocket_allowed_origin_receives_backend_events(client, origin):
    headers = {"Origin": origin} if origin is not None else {}
    with client.websocket_connect("/ws", headers=headers) as websocket:
        response = client.post("/runs", json={"request": "WebSocket test"})
        assert response.status_code == 202

        event = websocket.receive_json()
        assert event["type"] == "run_started"
        assert event["run_id"] == response.json()["run_id"]


def test_websocket_unknown_origin_is_rejected_before_accept(client):
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws", headers={"Origin": "https://unknown.example"}):
            pytest.fail("The WebSocket should not be accepted")

    assert rejected.value.code == 1008
