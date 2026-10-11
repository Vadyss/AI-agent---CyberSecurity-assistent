# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import re

import pytest

from orchestrator.events import EVENT_TYPES, EventEmitter, InternalOrderError
from orchestrator.run_store import RunState, RunStore
from orchestrator.ws import WebSocketHub
from tests.unit.api_support import METRICS


@pytest.mark.parametrize("text", ["<img src=x onerror=alert(1)>", "<script>alert(1)</script>",
                                  "**bold** [odkaz](javascript:alert(1))", "A" * 300])
async def test_untrusted_request_is_preserved_as_plain_json(text):
    run = RunState("run_aabb", text)
    class Hub:
        def broadcast(self, event, tenant_id):
            assert run.events[-1] == event
            assert run.last_seq == event["seq"]
    emitter = EventEmitter(Hub())
    event = await emitter.emit(run, "run_started", "intake", {"request": text})
    assert event["data"]["request"] == text
    assert event["seq"] == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", event["timestamp"])


async def test_order_guards_and_voice_after_terminal():
    run = RunState("run_aabb", "hello")
    emitter = EventEmitter(WebSocketHub())
    with pytest.raises(InternalOrderError):
        await emitter.emit(run, "plan_ready", "plan", {"steps": [], "skills_needed": []})
    await emitter.emit(run, "run_started", "intake", {"request": run.request})
    await emitter.emit(run, "plan_ready", "plan", {"steps": ["a" * 300] * 20, "skills_needed": ["a" * 50] * 5})
    assert len(run.events[-1]["message"]) <= 200
    assert len(run.events[-1]["data"]["steps"]) == 10
    with pytest.raises(InternalOrderError):
        await emitter.emit(run, "awaiting_approval", "approval", {"recipe": {"name": "abc"}, "metrics_tuning": METRICS,
                                                                  "metrics_validation": METRICS, "new_skills": []})
    await emitter.emit(run, "validation_done", "validation", {"dataset": "validation", "metrics": METRICS})
    await emitter.emit(run, "summary", "approval", {"text": "x" * 1200, "stats": run.stats.snapshot()})
    assert len(run.events[-1]["data"]["text"]) == 1000
    with pytest.raises(InternalOrderError):
        await emitter.emit(run, "plan_ready", "plan", {"steps": [], "skills_needed": []})
    await emitter.emit(run, "run_failed", "approval", {"reason_code": "INTERNAL_ERROR", "reason": "Error."})
    with pytest.raises(InternalOrderError):
        await emitter.emit(run, "summary", "approval", {"text": "hello", "stats": run.stats.snapshot()})
    await emitter.emit(run, "voice_ready", "approval", {"audio_url": "/api/runs/run_aabb/audio"})
    assert [e["seq"] for e in run.events] == list(range(1, run.last_seq + 1))
    assert run.finished_at is not None


async def test_no_installation_without_human_decision():
    run = RunState("run_aabb", "hello")
    emitter = EventEmitter(WebSocketHub())
    await emitter.emit(run, "run_started", "intake", {"request": run.request})
    with pytest.raises(InternalOrderError):
        await emitter.emit(run, "rule_approved", "done", {"rule_name": "abc", "comment": None})


def test_run_store_keeps_only_recent_finished_runs_in_memory():
    store = RunStore(max_runs=3)
    for n in range(6):
        for run in store.runs.values():
            run.status = "failed"
        store.adopt(f"run_{n:04d}abcd", "request", None, "local:local")
    assert len(store.runs) == 3
    # Active runs are never evicted, even beyond the bound.
    crowded = RunStore(max_runs=1)
    crowded.adopt("run_0000aaaa", "a", None, "x")
    crowded.adopt("run_0000bbbb", "b", None, "x")
    assert set(crowded.runs) == {"run_0000aaaa", "run_0000bbbb"}


def test_exact_event_catalog():
    assert EVENT_TYPES == {"run_started", "plan_ready", "skill_reused", "capability_missing", "forge_started",
                           "skill_tests_failed", "skill_candidate_ready", "rule_drafted", "rule_evaluated",
                           "validation_done", "summary", "voice_ready", "awaiting_approval", "skill_installed",
                           "rule_approved", "rule_rejected", "policy_rejected", "run_failed", "run_cancelled",
                           "llm_usage"}
