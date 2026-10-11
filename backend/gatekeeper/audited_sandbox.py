# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Every sandbox execution leaves a hash-chained audit record.

Records hold digests only: which tool version ran, and SHA-256 of the
inputs and the output. The audit log itself adds who caused it (the actor). Raw code, logs and results never enter the audit.
"""
from __future__ import annotations

import hashlib
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from .audit import AUDIT_ACTOR
from .recipe import canonical_json
from .types import SandboxError

__all__ = ["AUDIT_ACTOR", "AuditedSandbox", "audit_run", "digest"]
_RUN_ID: ContextVar[str | None] = ContextVar("audit_run_id", default=None)


@contextmanager
def audit_run(run_id: str | None) -> Iterator[None]:
    token = _RUN_ID.set(run_id)
    try:
        yield
    finally:
        _RUN_ID.reset(token)


def digest(value: Any) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else canonical_json(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AuditedSandbox:
    def __init__(self, inner, audit, describe: Callable[[str], dict | None], observer=None) -> None:
        self.inner = inner
        self.audit = audit
        self.describe = describe
        # Optional (mode, status, seconds) callback for metrics.
        self.observer = observer

    async def health(self) -> bool:
        return await self.inner.health()

    async def close(self) -> None:
        await self.inner.close()

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        """Raw job execution, audited like run and test (completes SandboxProtocol)."""
        raw_files = job.get("files")
        files: dict[str, Any] = raw_files if isinstance(raw_files, dict) else {}
        inputs = {"inputs": job.get("inputs"), "params": job.get("params"),
                  "tests_sha256": digest(files["test_skill.py"]) if isinstance(files.get("test_skill.py"), str) else None}
        return await self._execute(str(job.get("mode")), str(files.get("skill.py", "")), inputs, self.inner.execute(job))

    async def run(self, code: str, inputs: list, params: dict, *,
                  allowed_imports: list[str], timeout_s: float = 15) -> dict[str, Any]:
        return await self._execute("run", code, {"inputs": inputs, "params": params},
                                   self.inner.run(code, inputs, params, allowed_imports=allowed_imports,
                                                  timeout_s=timeout_s))

    async def test(self, code: str, tests: str, *,
                   allowed_imports: list[str], timeout_s: float = 10) -> dict[str, Any]:
        return await self._execute("test", code, {"tests_sha256": digest(tests)},
                                   self.inner.test(code, tests, allowed_imports=allowed_imports,
                                                   timeout_s=timeout_s))

    async def _execute(self, mode: str, code: str, inputs: dict, call) -> dict[str, Any]:
        started = time.monotonic()
        code_sha256 = digest(code)
        try:
            result = await call
        except SandboxError:
            self._record(mode, code_sha256, inputs, None, "transport_error", started)
            raise
        output = {key: value for key, value in result.items() if key != "duration_ms"}
        self._record(mode, code_sha256, inputs, output, result.get("status"), started)
        return result

    def _record(self, mode: str, code_sha256: str, inputs: dict, output: dict | None,
                status: str | None, started: float) -> None:
        if self.observer is not None:
            self.observer(mode, status, time.monotonic() - started)
        self.audit.append("sandbox_execution", _RUN_ID.get(), {
            "mode": mode,
            "tool": self.describe(code_sha256),
            "code_sha256": code_sha256,
            "input_sha256": digest(inputs),
            "output_sha256": None if output is None else digest(output),
            "status": status,
            "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
        })
