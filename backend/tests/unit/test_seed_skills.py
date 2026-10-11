# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fakes import InProcessSandbox

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("name,minimum", [("ssh_parser", 9), ("count_window", 7)])
async def test_seed_contract_in_real_runner(name, minimum):
    directory = ROOT / "seed_skills" / name
    manifest = json.loads((directory / "manifest.json").read_text())
    sandbox = InProcessSandbox()
    result = await sandbox.test((directory / "skill.py").read_text(), (directory / "test_skill.py").read_text(), allowed_imports=manifest["imports"])
    assert result["status"] == "ok", result
    assert result["tests"]["total"] >= minimum
    assert result["tests"]["failed"] == 0, result
