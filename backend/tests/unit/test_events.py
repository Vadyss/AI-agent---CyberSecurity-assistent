# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from __future__ import annotations

import asyncio
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


async def test_run_store_is_bounded_and_intake_is_atomic():
    store = RunStore(max_runs=3)
    results = await asyncio.gather(store.create("first"), store.create("second"), return_exceptions=True)
    assert sum(isinstance(r, RunState) for r in results) == 1
    for _ in range(5):
        for r in store.runs.values():
            r.status = "failed"
        await store.create("next")
    assert len(store.runs) == 3


def test_exact_event_catalog():
    assert EVENT_TYPES == {"run_started", "plan_ready", "skill_reused", "capability_missing", "forge_started",
                           "skill_tests_failed", "skill_candidate_ready", "rule_drafted", "rule_evaluated",
                           "validation_done", "summary", "voice_ready", "awaiting_approval", "skill_installed",
                           "rule_approved", "rule_rejected", "policy_rejected", "run_failed", "llm_usage"}
