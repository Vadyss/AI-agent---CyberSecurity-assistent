# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def test_gatekeeper_has_no_language_model_or_agent_dependency():
    forbidden = {"orchestrator", "examiner", "openai", "anthropic", "requests", "llm"}
    for path in (BACKEND / "gatekeeper").rglob("*.py"):
        for module in imports(path):
            assert module.split(".")[0] not in forbidden, (path.name, module)
            if module.split(".")[0] == "httpx":
                assert path.name == "sandbox_client.py"


def test_orchestrator_only_imports_public_authority():
    for path in (BACKEND / "orchestrator").rglob("*.py"):
        for module in imports(path):
            if module.startswith("gatekeeper"):
                assert module in {"gatekeeper.api", "gatekeeper.types"}, (path, module)
            if module.startswith("examiner"):
                assert path.name == "main.py"


def test_production_never_executes_generated_code_or_imports_tests():
    for package in ("gatekeeper", "orchestrator", "examiner"):
        for path in (BACKEND / package).rglob("*.py"):
            for module in imports(path):
                assert module.split(".")[0] not in {"tests", "importlib", "subprocess"}, (path, module)
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id not in {"exec", "eval"}, path


def test_only_registry_audit_lessons_write_runtime_data():
    for package in ("gatekeeper", "orchestrator", "examiner"):
        for path in (BACKEND / package).rglob("*.py"):
            if path.name in {"registry.py", "audit.py", "lessons.py"}:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in {"write_text", "write_bytes", "copytree", "rmtree", "move"}, path
                    if node.func.attr == "replace" and isinstance(node.func.value, ast.Name):
                        assert node.func.value.id != "os", path
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    modes = [n.value for n in node.args[1:] if isinstance(n, ast.Constant) and isinstance(n.value, str)]
                    modes += [k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)]
                    assert not any(any(c in m for c in "wax+") for m in modes), path


def test_sandbox_compose_is_isolated():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    sandbox = compose["services"]["sandbox"]
    assert sandbox["read_only"] is True
    assert "ALL" in sandbox["cap_drop"]
    # Only the capabilities the supervisor needs to switch to and clean up job UIDs.
    assert sorted(sandbox.get("cap_add", [])) == ["KILL", "SETGID", "SETUID"]
    assert "no-new-privileges:true" in sandbox["security_opt"]
    assert sandbox["init"] is True
    # The only mount is the socket volume shared with the backend.
    assert sandbox.get("volumes", []) == ["sandbox-socket:/run/toolsmith"]
    assert not sandbox.get("ports")
    assert sandbox["network_mode"] == "none" and "networks" not in sandbox
    assert sandbox["environment"]["SANDBOX_ISOLATION"] == "uid"
    assert any("mode=0700" in mount for mount in sandbox["tmpfs"])
    env = sandbox.get("environment", {})
    for name in env:
        assert not any(secret in name.upper() for secret in ("KEY", "TOKEN", "SECRET"))
    assert not any("docker.sock" in str(v) for s in compose["services"].values() for v in s.get("volumes", []))
