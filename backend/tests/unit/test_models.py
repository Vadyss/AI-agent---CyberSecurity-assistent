# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from orchestrator.models import DATA_MODELS, Metrics


def test_every_contract_payload_example_is_accepted():
    text = (Path(__file__).resolve().parents[3] / "Docs" / "kontrakt.md").read_text()
    section = re.search(r"^## 10\..*?(?=^## 11\.)", text, re.M | re.S).group()
    count = 0
    for name, content in re.findall(r"^#### `([^`]+)`(.*?)(?=^#{2,4} |\Z)", section, re.M | re.S):
        for sample in re.findall(r"```json\s*(.*?)\s*```", content, re.S):
            DATA_MODELS[name].model_validate(json.loads(sample))
            count += 1
    assert count == 18


def test_null_metrics_and_exact_fields():
    value = Metrics(true_positives=0, false_positives=0, false_negatives=8,
                    precision=None, recall=0, passed=False,
                    thresholds={"min_precision": .9, "min_recall": .9})
    assert value.model_dump(mode="json")["precision"] is None
    with pytest.raises(ValidationError):
        Metrics.model_validate({**value.model_dump(), "recall": 1.1})


def test_payload_text_limits_and_control_cleanup():
    data = DATA_MODELS["plan_ready"](steps=["A" * 201 + "\x00"] * 20, skills_needed=[])
    assert len(data.steps) == 10
    assert all(len(s) == 200 and s.endswith("…") for s in data.steps)
    data = DATA_MODELS["rule_drafted"](attempt=1, max_attempts=3,
                                        recipe={"name": "abc"}, explanation="B" * 1100)
    assert len(data.explanation) == 1000
