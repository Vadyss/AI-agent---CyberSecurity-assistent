# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""The evaluation harness itself works; the mock provider must score perfectly."""
from __future__ import annotations

from evals.run import evaluate


def test_mock_evaluation_scores_every_role():
    report = evaluate("mock", timeout_s=60)
    summary = report["summary"]
    assert summary["cases"] == 5
    assert summary["end_to_end_pass_rate"] == 1.0, [r for r in report["results"] if not r["passed"]]
    for metric in ("planner_accuracy", "forge_success_rate", "rule_validation_pass_rate", "safety_pass_rate"):
        assert summary[metric] == 1.0, metric
    assert 0 < summary["forge_first_try_rate"] <= 1
    assert set(report["prompts"]) >= {"planner", "forge", "rule_author"}
    injection = next(r for r in report["results"] if r["id"] == "bruteforce-with-injection")
    assert injection["rule"]["attempts"] == 2  # The gatekeeper refused the first draft's IP exception.
