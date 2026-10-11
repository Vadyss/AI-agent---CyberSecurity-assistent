# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_checkout_has_one_documentation_root_and_no_empty_backend_apps():
    docs = [p for p in ROOT.iterdir() if p.is_dir() and p.name.lower() == "docs"]
    assert len(docs) == 1
    assert (docs[0] / "kontrakt.md").is_file()
    for package in ("orchestrator", "gatekeeper"):
        assert not (ROOT / "backend" / package / "Dockerfile").exists()
        assert not (ROOT / "backend" / package / "requirements.txt").exists()
    assert not (ROOT / "backend/gatekeeper/main.py").exists()


def test_ci_keeps_all_test_suites_and_never_deletes_deployment_volumes():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    assert {"test", "frontend-test", "integration", "deploy"} <= workflow["jobs"].keys()
    suites = str(workflow["jobs"]["test"])
    # Backend and sandbox suites run through the shared Docker test script.
    assert "sh backend/scripts/test.sh" in suites
    script = (ROOT / "backend/scripts/test.sh").read_text()
    assert "../sandbox/requirements.txt" in script and "../sandbox/tests" in script
    assert "apify/llm-relay" in suites and "pytest tests" in suites
    deploy = str(workflow["jobs"]["deploy"])
    assert "down -v" not in deploy and "--volumes" not in deploy
    for check in ("APIFY_TOKEN", "http://1.1.1.1", "touch /app/test", "/health"):
        assert check in deploy
    assert "npm test" in str(workflow["jobs"]["frontend-test"])
    integration = str(workflow["jobs"]["integration"])
    assert "--base http://localhost:13000" in integration
    assert "--idle-before-run 180" in integration


def test_readme_local_document_links_exist():
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", (ROOT / "README.md").read_text()):
        if "://" not in target:
            assert (ROOT / target).exists(), target


def test_backend_image_contains_every_runtime_package():
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text()
    # tests and evals are development tooling; evals needs the repository's sandbox package.
    packages = sorted(p.name for p in (ROOT / "backend").iterdir()
                      if (p / "__init__.py").is_file() and p.name not in {"tests", "evals"})
    assert packages, "No backend packages found."
    for package in packages:
        assert f"COPY {package}/ ./{package}/" in dockerfile, package


def test_lock_files_pin_exactly_the_declared_versions():
    import re
    for folder in ("backend", "sandbox"):
        declared = {}
        for line in (ROOT / folder / "requirements.txt").read_text().splitlines():
            match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;]+)", line.strip())
            if match:
                declared[match.group(1).lower().replace("_", "-")] = match.group(2)
        lock = (ROOT / folder / "requirements.lock").read_text()
        locked = {m.group(1).lower().replace("_", "-"): m.group(2)
                  for m in re.finditer(r"^([A-Za-z0-9_.-]+)==(\S+) \\$", lock, re.M)}
        assert declared, folder
        for name, version in declared.items():
            assert locked.get(name) == version, (folder, name, version, locked.get(name))
        assert lock.count("--hash=sha256:") >= len(locked), folder
    for dockerfile in ("backend/Dockerfile", "sandbox/Dockerfile"):
        text = (ROOT / dockerfile).read_text()
        assert "--require-hashes" in text and "@sha256:" in text, dockerfile
