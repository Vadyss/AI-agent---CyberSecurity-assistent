# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import httpx
import pytest

from gatekeeper.sandbox_client import SandboxClient
from gatekeeper.types import SandboxError
from tests.fakes import InProcessSandbox


def result(**changes):
    data = {"status": "ok", "result": [], "tests": {"total": 0, "failed": 0, "failures": []},
            "error": None, "duration_ms": 12}
    data.update(changes)
    return data


async def test_run_test_and_health_protocol():
    seen = []
    async def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"status": "ok"} if request.url.path == "/health" else result())
    client = SandboxClient(transport=httpx.MockTransport(handler))
    assert await client.health() is True
    assert await client.run("code", [1], {"x": 2}, allowed_imports=["re"], timeout_s=15) == result()
    assert await client.test("code", "tests", allowed_imports=["collections"]) == result()
    assert seen[1].url.path == "/execute"
    import json
    payload = json.loads(seen[1].content)
    assert payload["files"] == {"skill.py": "code"}
    assert payload["inputs"] == [1] and payload["params"] == {"x": 2}
    assert payload["entrypoint"] == "run" and payload["mode"] == "run"
    assert json.loads(seen[2].content)["files"]["test_skill.py"] == "tests"
    await client.close()
    assert client.client.is_closed


@pytest.mark.parametrize("status", ["error", "timeout", "rejected"])
async def test_skill_job_failures_are_results_not_transport_exceptions(status):
    async def handler(request):
        return httpx.Response(200, json=result(status=status, result=None, error="chyba"))
    client = SandboxClient(transport=httpx.MockTransport(handler))
    assert (await client.run("", [], {}, allowed_imports=[]))["status"] == status
    await client.close()


@pytest.mark.parametrize("reply", [
    httpx.Response(503, text="PRIVATE_SERVER_DETAIL"), httpx.Response(400),
    httpx.Response(200, text="not-json PRIVATE_SERVER_DETAIL"),
    httpx.Response(200, json=[]), httpx.Response(200, json={"status": "ok"}),
    httpx.Response(200, json=result(status="unknown")),
    httpx.Response(200, json=result(status=[])),
    httpx.Response(200, json=result(result={})),
    httpx.Response(200, json=result(duration_ms=True)),
    httpx.Response(200, json=result(tests={"total": 1, "failed": 2, "failures": []})),
    httpx.Response(200, json=result(tests={"total": 1, "failed": 1, "failures": [{"name": 42, "error": "x"}]})),
    httpx.Response(200, json=result(error="x" * 2001)),
    httpx.Response(200, text="x" * 10_000_001),
])
async def test_http_or_invalid_protocol_raises_sanitized_sandbox_error(reply):
    async def handler(request):
        return reply
    client = SandboxClient(transport=httpx.MockTransport(handler))
    with pytest.raises(SandboxError) as error:
        await client.execute({})
    assert "PRIVATE_SERVER_DETAIL" not in str(error.value)
    await client.close()


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
async def test_network_errors_and_health(error_type):
    async def handler(request):
        raise error_type("PRIVATE_SERVER_DETAIL")
    client = SandboxClient(transport=httpx.MockTransport(handler))
    assert await client.health() is False
    with pytest.raises(SandboxError) as error:
        await client.execute({})
    assert "PRIVATE_SERVER_DETAIL" not in str(error.value)
    await client.close()


async def test_fake_uses_actual_runner_and_same_runtime_guard():
    sandbox = InProcessSandbox()
    assert (await sandbox.run("def run(inputs, params):\n    return inputs", [1], {}, allowed_imports=[]))["result"] == [1]
    blocked = await sandbox.run("import socket\ndef run(inputs, params):\n    return []", [], {}, allowed_imports=[])
    assert blocked["status"] == "error" and "Zakázaný import" in blocked["error"]
    assert await sandbox.health()
    await sandbox.close()
