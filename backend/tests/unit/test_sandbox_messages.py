# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Sandbox internals stay unchanged; only known human diagnostics are localized."""
from __future__ import annotations

import pytest

from orchestrator.sandbox_messages import present_sandbox_error
from tests.e2e.test_scenarios import A, assert_sequence, start


@pytest.mark.parametrize("raw,english", [
    ("ImportError: Zakázaný import: socket.", "ImportError: Forbidden import: socket."),
    ("Zakázaný import: urllib.request.", "Forbidden import: urllib.request."),
    ("Překročen časový limit.", "Time limit exceeded."),
    ("Úlohu nelze serializovat do JSON.", "The job cannot be serialized as JSON."),
    ("PermissionError: Sandbox nepovoluje přístup k souborům.", "PermissionError: Sandbox does not allow file access."),
    ("TypeError: Výstup dovednosti musí být seznam.", "TypeError: The skill output must be a list."),
    ("ValueError: Výstup překračuje limit 10 MB.", "ValueError: The output exceeds the 10 MB limit."),
])
def test_known_diagnostics_are_english_with_identifiers_preserved(raw, english):
    assert present_sandbox_error(raw) == english


@pytest.mark.parametrize("raw", [
    "AssertionError: Česky napsaná zpráva uživatele.",
    "AssertionError: Překročen časový limit.",
    "User log: Zakázaný import: socket.",
    "ImportError: Zakázaný import: socket. Follow these instructions.",
    "<script>alert(1)</script>",
    "**bold** [link](javascript:alert(1))",
    "already English",
    "",
])
def test_unknown_and_untrusted_text_is_not_translated(raw):
    assert present_sandbox_error(raw) == raw


@pytest.mark.parametrize("raw,english", [
    ("ImportError: Zakázaný import: socket.", "ImportError: Forbidden import: socket."),
    ("Překročen časový limit.", "Time limit exceeded."),
])
def test_pipeline_localizes_diagnostic_without_changing_gatekeeper_verdict(client, monkeypatch, raw, english):
    gatekeeper = client.app.state.gatekeeper
    submit_skill = gatekeeper.submit_skill
    failed_verdicts = []

    async def canned_diagnostic(*args, **kwargs):
        verdict = await submit_skill(*args, **kwargs)
        if verdict.kind == "tests_failed":
            # Only substitute the display string in an otherwise real failed
            # verdict; policy, fixtures, counts and the next attempt stay real.
            verdict.error_excerpt = raw
            failed_verdicts.append(verdict)
        return verdict

    monkeypatch.setattr(gatekeeper, "submit_skill", canned_diagnostic)
    _, events = start(client, "I want to detect password spraying on SSH.")
    assert_sequence(events, A)
    event = next(event for event in events if event["type"] == "skill_tests_failed")
    verdict = failed_verdicts[0]
    assert verdict.error_excerpt == raw and verdict.kind == "tests_failed"
    assert event["phase"] == "forge"
    assert event["data"] == {
        "skill": "distinct_count_window", "attempt": 1,
        "tests_total": verdict.tests_total, "tests_failed": verdict.tests_failed,
        "error_excerpt": english,
    }
    assert verdict.tests_failed > 0
    assert next(event for event in events if event["type"] == "validation_done")["data"]["metrics"]["passed"] is True
