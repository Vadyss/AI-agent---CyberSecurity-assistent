# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gatekeeper.datasets import DatasetStore
from gatekeeper.evaluator import evaluate_recipe
from gatekeeper.policy import load_policy
from gatekeeper.static_analysis import analyze_code
from orchestrator.llm_mock import fixture, manifest
from tests.fakes import InProcessSandbox

ROOT = Path(__file__).resolve().parents[2]


def load_skill(name):
    seed_dir = ROOT / "seed_skills" / name
    if seed_dir.exists():
        return ((seed_dir / "skill.py").read_text(), json.loads((seed_dir / "manifest.json").read_text()), {"origin": "seed"})
    return fixture(name + ".py"), manifest(name), {"origin": "agent"}


def recipe(attack_type, bad=False):
    distributed = attack_type == "ssh_distributed_bruteforce"
    distinct = attack_type in {"ssh_password_spraying", "ssh_distributed_bruteforce"}
    web = attack_type == "web_dir_bruteforce"
    params = {"group_by": "user" if distributed else "src_ip", "window_s": 300}
    if distinct:
        params["distinct_field"] = "src_ip" if distributed else "user"
    return {"name": attack_type, "attack_type": attack_type,
            "parser": "nginx_access_parser" if web else "ssh_parser",
            "filter": [{"field": "status" if web else "outcome", "op": "eq", "value": 404 if web else "failure"}],
            "aggregation": {"skill": "distinct_count_window" if distinct else "count_window", "params": params},
            "condition": {"field": "distinct_count" if distinct else "count", "op": "gte",
                          "value": 25 if bad else 20 if web else 5 if distinct else 10}}


@pytest.mark.parametrize("dataset", ["tuning", "validation"])
@pytest.mark.parametrize("attack_type,count", [("ssh_password_spraying", 8), ("ssh_distributed_bruteforce", 6),
                                              ("ssh_bruteforce", 8), ("web_dir_bruteforce", 6)])
async def test_recipes_calibrated_against_real_runner(dataset, attack_type, count):
    data = DatasetStore(ROOT / "datasets").load("web" if attack_type.startswith("web_") else "ssh", dataset)
    result = await evaluate_recipe(recipe(attack_type), data.lines, data.labels, load_skill,
                                   InProcessSandbox(), load_policy(ROOT / "policy/policy.yaml"), feedback=dataset == "tuning")
    assert result.metrics.true_positives == count
    assert result.metrics.false_positives == 0 and result.metrics.false_negatives == 0
    assert result.metrics.precision == 1 and result.metrics.recall == 1 and result.metrics.passed


async def test_high_first_spraying_threshold_really_misses_attacks():
    data = DatasetStore(ROOT / "datasets").load("ssh", "tuning")
    result = await evaluate_recipe(recipe("ssh_password_spraying", bad=True), data.lines, data.labels,
                                   load_skill, InProcessSandbox(), load_policy(ROOT / "policy/policy.yaml"), feedback=True)
    assert result.metrics.true_positives == 0 and result.metrics.false_positives == 0
    assert result.metrics.false_negatives == 8 and result.metrics.precision is None and result.metrics.recall == 0
    assert not result.metrics.passed
    assert len(result.feedback_examples) == 3
    assert not any(ip in example for instance in data.labels["instances"] for ip in instance["src_ips"] for example in result.feedback_examples)


@pytest.mark.parametrize("name,tests", [("distinct_count_window", "distinct_tests.py"),
                                      ("nginx_access_parser", "nginx_tests.py"),
                                      ("failure_ratio_window", "failure_ratio_tests.py")])
def test_fixtures_pass_static_checks(name, tests):
    policy = load_policy(ROOT / "policy/policy.yaml")
    assert analyze_code(fixture(name + ".py"), manifest(name), policy) == []
    assert analyze_code(fixture(tests), manifest(name), policy, is_test=True) == []
    if name == "distinct_count_window":
        assert analyze_code(fixture("distinct_count_window_bad.py"), manifest(name), policy) == []
    if name == "failure_ratio_window":
        violations = analyze_code("import socket\n" + fixture(name + ".py"), manifest(name), policy)
        assert any(violation.code == "FORBIDDEN_IMPORT" for violation in violations)
