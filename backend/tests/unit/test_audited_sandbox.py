# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from __future__ import annotations

import hashlib
import json

import pytest

from gatekeeper.audit import AuditLog, verify_audit
from gatekeeper.audited_sandbox import AUDIT_ACTOR, AuditedSandbox, audit_run, digest
from gatekeeper.types import SandboxError

CODE = "def run(inputs, params):\n    return inputs\n"
OK = {"status": "ok", "result": [1], "tests": {"total": 0, "failed": 0, "failures": []}, "error": None, "duration_ms": 5}


class FakeInner:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.closed = answer or OK, fail, False

    async def health(self):
        return True

    async def run(self, code, inputs, params, *, allowed_imports, timeout_s=15):
        if self.fail:
            raise SandboxError("down")
        return dict(self.answer)

    async def test(self, code, tests, *, allowed_imports, timeout_s=10):
        return dict(self.answer)

    async def close(self):
        self.closed = True


def records(tmp_path):
    return [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]


async def test_run_records_actor_tool_and_digests_but_no_content(tmp_path):
    audit = AuditLog(tmp_path)
    sandbox = AuditedSandbox(FakeInner(), audit, lambda sha: {"name": "ssh_parser", "version": 2} if sha == digest(CODE) else None)
    token = AUDIT_ACTOR.set("user:analyst@example.test")
    try:
        with audit_run("run_abcd1234"):
            assert await sandbox.run(CODE, ["secret log line"], {"window_s": 60}, allowed_imports=[]) == OK
    finally:
        AUDIT_ACTOR.reset(token)
    record = records(tmp_path)[-1]
    assert record["kind"] == "sandbox_execution" and record["run_id"] == "run_abcd1234"
    detail = record["detail"]
    assert record["actor"] == "user:analyst@example.test"
    assert detail["tool"] == {"name": "ssh_parser", "version": 2}
    assert detail["code_sha256"] == hashlib.sha256(CODE.encode()).hexdigest()
    assert detail["input_sha256"] == digest({"inputs": ["secret log line"], "params": {"window_s": 60}})
    assert detail["output_sha256"] == digest({k: v for k, v in OK.items() if k != "duration_ms"})
    assert detail["status"] == "ok" and detail["mode"] == "run"
    assert "secret log line" not in (tmp_path / "audit.jsonl").read_text()
    assert verify_audit(tmp_path / "audit.jsonl")[0]


async def test_test_mode_hashes_the_test_source(tmp_path):
    sandbox = AuditedSandbox(FakeInner(), AuditLog(tmp_path), lambda sha: None)
    await sandbox.test(CODE, "def test_x():\n    assert True\n", allowed_imports=[])
    detail = records(tmp_path)[-1]["detail"]
    assert detail["mode"] == "test" and detail["tool"] is None
    assert detail["input_sha256"] == digest({"tests_sha256": digest("def test_x():\n    assert True\n")})


async def test_transport_failure_is_recorded_and_reraised(tmp_path):
    sandbox = AuditedSandbox(FakeInner(fail=True), AuditLog(tmp_path), lambda sha: None)
    with pytest.raises(SandboxError):
        await sandbox.run(CODE, [], {}, allowed_imports=[])
    detail = records(tmp_path)[-1]["detail"]
    assert detail["status"] == "transport_error" and detail["output_sha256"] is None


async def test_health_and_close_are_delegated(tmp_path):
    inner = FakeInner()
    sandbox = AuditedSandbox(inner, AuditLog(tmp_path), lambda sha: None)
    assert await sandbox.health() is True
    await sandbox.close()
    assert inner.closed
