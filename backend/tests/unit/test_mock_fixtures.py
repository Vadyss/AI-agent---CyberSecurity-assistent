# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import pytest

from orchestrator.llm_mock import fixture, manifest
from tests.fakes import InProcessSandbox


@pytest.mark.parametrize("name,test_file", [("distinct_count_window", "distinct_tests.py"), ("nginx_access_parser", "nginx_tests.py")])
async def test_valid_fixtures_pass_real_sandbox(name, test_file):
    sandbox = InProcessSandbox()
    result = await sandbox.test(fixture(name + ".py"), fixture(test_file), allowed_imports=manifest(name)["imports"])
    assert result["status"] == "ok", result
    assert result["tests"]["total"] >= 4 and result["tests"]["failed"] == 0, result


async def test_first_forge_attempt_really_fails_window_test():
    sandbox = InProcessSandbox()
    code = fixture("distinct_count_window.py").replace("events[left][ts_field] < end - window", "events[left][ts_field] <= end - window")
    result = await sandbox.test(code, fixture("distinct_tests.py"), allowed_imports=["collections"])
    assert result["tests"]["failed"] == 1
    assert result["tests"]["failures"][0]["name"] == "test_window_boundary"


async def test_failure_ratio_fixture_really_fails():
    result = await InProcessSandbox().test(fixture("failure_ratio_window.py"), fixture("failure_ratio_tests.py"), allowed_imports=[])
    assert result["tests"]["total"] == 4 and result["tests"]["failed"] == 2
