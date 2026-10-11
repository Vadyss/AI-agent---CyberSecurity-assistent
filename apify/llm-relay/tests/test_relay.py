# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Offline relay tests: HTTP bodies stay opaque and secrets stay server-side."""
from __future__ import annotations

import asyncio
import gzip
import importlib.util
import json
import logging
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("apify_relay_main_under_test", ROOT / "main.py")
relay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(relay)

FIXED_UPSTREAM = "https://openrouter.apify.actor/api/v1/chat/completions"
TOKEN = "actor-server-token-must-stay-private"
CALLER_TOKEN = "caller-token-must-never-reach-upstream"
PROMPT = "private-prompt-never-logged"


@pytest.fixture(autouse=True)
def private_environment(monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", TOKEN)
    # No proxy variables may alter the fixed upstream or leak Actor credentials.
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted-proxy.invalid:8080")
    monkeypatch.setenv("ACTOR_WEB_SERVER_PORT", "24567")


def response(status: int = 200, body: bytes = b'{"ok":true}', headers=None):
    return httpx.Response(status, stream=httpx.ByteStream(body), headers=headers or {"content-type": "application/json"})


@pytest.mark.parametrize("body", [
    b'  { "messages": [{"role": "user", "content": "private"}], "model": "x" }\n',
    b'not JSON: \x00\xff\xfe\n',
    b'{"escaped":"\\u010d", "stream": false, "unknown_nested": {"a": [1, 2]}}',
    b'',
])
def test_request_bytes_fixed_url_and_server_authorization(body, monkeypatch):
    monkeypatch.setenv("APIFY_TOKEN", "  " + TOKEN + "\n")
    received = []

    async def upstream(request):
        received.append(request)
        return response()

    with TestClient(relay.create_app(transport=httpx.MockTransport(upstream))) as client:
        result = client.post("/v1/chat/completions?upstream=https://attacker.invalid", content=body,
                             headers={"Authorization": "Bearer " + CALLER_TOKEN,
                                      "Content-Type": "application/vnd.custom+json; charset=utf-8",
                                      "Accept": "application/json",
                                      "X-Upstream-URL": "https://attacker.invalid/private"})
        assert result.status_code == 200
    assert len(received) == 1
    request = received[0]
    assert request.method == "POST" and str(request.url) == FIXED_UPSTREAM
    assert request.content == body
    assert request.headers["authorization"] == "Bearer " + TOKEN
    assert CALLER_TOKEN not in str(request.headers)
    assert request.headers["content-type"] == "application/vnd.custom+json; charset=utf-8"
    assert request.headers["accept"] == "application/json"
    assert "x-upstream-url" not in request.headers


@pytest.mark.parametrize("status", [200, 201, 400, 401, 403, 404, 429, 500, 502, 503])
def test_upstream_status_body_and_content_type_preserved(status):
    raw = b' \x00\xff upstream bytes with whitespace\n'

    async def upstream(request):
        return response(status, raw, {"content-type": "application/octet-stream", "x-request-id": "upstream-id"})

    with TestClient(relay.create_app(transport=httpx.MockTransport(upstream))) as client:
        result = client.post("/v1/chat/completions", content=b'{}')
        assert result.status_code == status and result.content == raw
        assert result.headers["content-type"] == "application/octet-stream"
        assert result.headers["x-request-id"] == "upstream-id"


def test_empty_success_and_redirect_are_passed_without_following():
    statuses = iter([204, 307])
    calls = []

    async def upstream(request):
        calls.append(str(request.url))
        status = next(statuses)
        return response(status, b"" if status == 204 else b"redirect bytes",
                        {"location": "https://another-host.invalid/", "content-type": "text/plain"})

    with TestClient(relay.create_app(transport=httpx.MockTransport(upstream))) as client:
        empty = client.post("/v1/chat/completions", content=b'{}')
        assert empty.status_code == 204 and empty.content == b""
        redirected = client.post("/v1/chat/completions", content=b'{}', follow_redirects=False)
        assert redirected.status_code == 307 and redirected.content == b"redirect bytes"
        assert redirected.headers["location"] == "https://another-host.invalid/"
    assert calls == [FIXED_UPSTREAM, FIXED_UPSTREAM]


def test_compressed_request_and_response_preserve_raw_bytes():
    request_body = gzip.compress(b'{ "messages": [], "stream": false }', mtime=0)
    upstream_body = gzip.compress(b'raw compressed response', mtime=0)
    captured = []

    async def upstream(request):
        captured.append(request)
        return response(body=upstream_body, headers={"content-type": "application/octet-stream", "content-encoding": "gzip", "content-length": str(len(upstream_body))})

    with TestClient(relay.create_app(transport=httpx.MockTransport(upstream))) as client:
        with client.stream("POST", "/v1/chat/completions", content=request_body,
                           headers={"Content-Encoding": "gzip", "Content-Type": "application/json"}) as result:
            assert result.status_code == 200
            assert b"".join(result.iter_raw()) == upstream_body
            assert result.headers["content-encoding"] == "gzip"
            assert result.headers["content-length"] == str(len(upstream_body))
    assert captured[0].content == request_body and captured[0].headers["content-encoding"] == "gzip"


def test_hop_by_hop_headers_and_connection_named_headers_removed():
    async def upstream(request):
        return response(headers={"content-type": "application/json", "connection": "keep-alive, x-private",
                                 "keep-alive": "timeout=30", "x-private": "must be removed",
                                 "transfer-encoding": "chunked", "proxy-authenticate": "private",
                                 "x-request-id": "preserved"})

    with TestClient(relay.create_app(transport=httpx.MockTransport(upstream))) as client:
        result = client.post("/v1/chat/completions", content=b'{}')
        for name in ("connection", "keep-alive", "x-private", "transfer-encoding", "proxy-authenticate"):
            assert name not in result.headers
        assert result.headers["x-request-id"] == "preserved"


def test_readiness_routes_and_timeout_client_configuration():
    def forbidden_upstream(request):
        raise AssertionError("Readiness must not call the LLM upstream.")

    with TestClient(relay.create_app(transport=httpx.MockTransport(forbidden_upstream))) as client:
        assert client.get("/").json() == {"status": "ok"}
        assert client.get("/health").json() == {"status": "ok"}
        timeout = client.app.state.client.timeout
        assert timeout.connect == timeout.read == timeout.write == timeout.pool == 180
        assert not client.app.state.client.follow_redirects
        assert not client.app.state.client.trust_env
        assert relay.TIMEOUT_S == 180.0


@pytest.mark.parametrize("token", [None, "", " \n\t"])
def test_missing_actor_token_refuses_startup(token, monkeypatch):
    if token is None:
        monkeypatch.delenv("APIFY_TOKEN", raising=False)
    else:
        monkeypatch.setenv("APIFY_TOKEN", token)
    with pytest.raises(RuntimeError, match="APIFY_TOKEN"):
        with TestClient(relay.create_app(transport=httpx.MockTransport(lambda request: response()))):
            pass


@pytest.mark.parametrize("error,status", [
    (httpx.ReadTimeout, 504),
    (httpx.ConnectTimeout, 504),
    (httpx.WriteTimeout, 504),
    (httpx.PoolTimeout, 504),
    (httpx.ConnectError, 502),
    (httpx.RemoteProtocolError, 502),
])
def test_gateway_errors_are_safe_and_do_not_log_secrets(error, status, caplog):
    caplog.set_level(logging.DEBUG)

    async def upstream(request):
        raise error(f"Sensitive transport detail: {TOKEN} {PROMPT}")

    with TestClient(relay.create_app(transport=httpx.MockTransport(upstream))) as client:
        result = client.post("/v1/chat/completions", content=PROMPT.encode(), headers={"Authorization": CALLER_TOKEN})
    assert result.status_code == status
    assert result.json() == {"error": {"message": "Upstream request timed out." if status == 504 else "Upstream request failed."}}
    for protected in (TOKEN, CALLER_TOKEN, PROMPT, "Sensitive transport detail"):
        assert protected not in result.text and protected not in caplog.text


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"first"
        await asyncio.sleep(.1)
        yield b"second"


def test_total_response_deadline_handles_slow_chunks(monkeypatch):
    # Reduce the configured deadline for the test; production is checked at 120s.
    monkeypatch.setattr(relay, "TIMEOUT_S", .02)

    async def upstream(request):
        return httpx.Response(200, stream=SlowStream())

    with TestClient(relay.create_app(transport=httpx.MockTransport(upstream))) as client:
        result = client.post("/v1/chat/completions", content=b'{}')
    assert result.status_code == 504
    assert result.json() == {"error": {"message": "Upstream request timed out."}}


def test_startup_uses_actor_web_server_port_and_disables_logging(monkeypatch):
    calls = []
    disabled = []
    monkeypatch.setenv("ACTOR_WEB_SERVER_PORT", "24567")
    monkeypatch.setattr(relay.logging, "disable", disabled.append)
    monkeypatch.setattr(relay.uvicorn, "run", lambda app, **kwargs: calls.append((app, kwargs)))
    relay.main()
    assert len(calls) == 1
    assert calls[0][1] == {"host": "0.0.0.0", "port": 24567, "access_log": False, "log_config": None}
    assert disabled == [logging.CRITICAL]


@pytest.mark.parametrize("port", [None, "0", "-1", "65536", "invalid", "8000; unwanted-command"])
def test_invalid_actor_port_refuses_startup(port, monkeypatch):
    if port is None:
        monkeypatch.delenv("ACTOR_WEB_SERVER_PORT", raising=False)
    else:
        monkeypatch.setenv("ACTOR_WEB_SERVER_PORT", port)
    monkeypatch.setattr(relay.logging, "disable", lambda level: None)
    monkeypatch.setattr(relay.uvicorn, "run", lambda *args, **kwargs: pytest.fail("Server must not start with an invalid Actor port."))
    with pytest.raises(SystemExit, match="ACTOR_WEB_SERVER_PORT"):
        relay.main()


def test_actor_metadata_declares_standby_mode():
    actor = json.loads((ROOT / ".actor/actor.json").read_text(encoding="utf-8"))
    assert actor["usesStandbyMode"] is True
