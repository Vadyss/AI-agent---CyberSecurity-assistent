# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Typed HTTP transport; skill failures are data, transport failures exceptions."""
from __future__ import annotations

import json
import math
from typing import Any
from uuid import uuid4

import httpx

from .types import SandboxError

MAX_RESPONSE_BYTES = 10_000_000


def validate_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("status") not in ("ok", "error", "timeout", "rejected"):
        raise SandboxError("The sandbox returned an invalid job status.")
    if set(value) != {"status", "result", "tests", "error", "duration_ms"}:
        raise SandboxError("The sandbox returned an invalid job envelope.")
    duration = value["duration_ms"]
    if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
        raise SandboxError("The sandbox returned an invalid job duration.")
    error = value["error"]
    if error is not None and (not isinstance(error, str) or len(error) > 2000):
        raise SandboxError("The sandbox returned an invalid error description.")
    if value["result"] is not None and not isinstance(value["result"], list):
        raise SandboxError("The sandbox returned invalid skill output.")
    tests = value["tests"]
    if not isinstance(tests, dict) or set(tests) != {"total", "failed", "failures"}:
        raise SandboxError("The sandbox returned an invalid test result.")
    if any(isinstance(tests[k], bool) or not isinstance(tests[k], int) or tests[k] < 0 for k in ("total", "failed")):
        raise SandboxError("The sandbox returned invalid test counts.")
    if tests["failed"] > tests["total"] or not isinstance(tests["failures"], list):
        raise SandboxError("The sandbox returned invalid failure counts.")
    for failure in tests["failures"]:
        if not isinstance(failure, dict) or set(failure) != {"name", "error"} or any(not isinstance(v, str) for v in failure.values()):
            raise SandboxError("The sandbox returned an invalid test failure.")
        if len(failure["name"]) > 200 or len(failure["error"]) > 500:
            raise SandboxError("The sandbox returned an excessively long test failure.")
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise SandboxError("The sandbox returned a result that cannot be serialized.") from exc
    return value


class SandboxClient:
    def __init__(self, base_url: str = "http://sandbox:8000", timeout_s: float = 30,
                 *, transport: httpx.AsyncBaseTransport | None = None, uds: str | None = None) -> None:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("The client timeout must be positive.")
        self.timeout_s = timeout_s
        if transport is None and uds:
            # The sandbox has no network at all; it listens on a Unix socket.
            transport = httpx.AsyncHTTPTransport(uds=uds)
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_s,
                                        transport=transport, trust_env=False)
        self.tenant_id: str | None = None
        self._owns_client = True

    def for_tenant(self, tenant_id: str) -> SandboxClient:
        """A view that shares the connection pool and stamps every job with the tenant."""
        view = object.__new__(SandboxClient)
        view.timeout_s, view.client, view.tenant_id, view._owns_client = self.timeout_s, self.client, tenant_id, False
        return view

    def _stamp(self, job: dict[str, Any]) -> dict[str, Any]:
        return {**job, "tenant_id": self.tenant_id} if self.tenant_id else job

    async def health(self) -> bool:
        try:
            answer = await self.client.get("/health", timeout=min(5, self.timeout_s))
            return answer.status_code == 200 and answer.json() == {"status": "ok"}
        except (httpx.HTTPError, ValueError):
            return False

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self.client.stream("POST", "/execute", json=job) as answer:
                if answer.status_code != 200:
                    raise SandboxError("The sandbox is unavailable or rejected the HTTP protocol.")
                body = bytearray()
                async for chunk in answer.aiter_bytes():
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise SandboxError("The sandbox response exceeds the size limit.")
                    body.extend(chunk)
            value = json.loads(body)
        except SandboxError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, RecursionError) as exc:
            # Do not expose exception URLs, headers or attacker-controlled body.
            raise SandboxError("The sandbox is unavailable or returned an invalid response.") from exc
        return validate_response(value)

    async def run(self, code: str, inputs: list, params: dict, *,
                  allowed_imports: list[str], timeout_s: float = 15) -> dict[str, Any]:
        return await self.execute(self._stamp({"job_id": uuid4().hex, "mode": "run", "files": {"skill.py": code},
                                               "entrypoint": "run", "inputs": inputs, "params": params,
                                               "allowed_imports": allowed_imports, "timeout_s": timeout_s}))

    async def test(self, code: str, tests: str, *,
                   allowed_imports: list[str], timeout_s: float = 10) -> dict[str, Any]:
        return await self.execute(self._stamp({"job_id": uuid4().hex, "mode": "test",
                                               "files": {"skill.py": code, "test_skill.py": tests},
                                               "entrypoint": "run", "inputs": [], "params": {},
                                               "allowed_imports": allowed_imports, "timeout_s": timeout_s}))

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()
