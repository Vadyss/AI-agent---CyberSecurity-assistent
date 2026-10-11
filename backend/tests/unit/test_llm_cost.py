# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Provider-reported USD cost is counted without changing model parsing or policy."""
from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from gatekeeper.types import ParseFailure
from orchestrator.config import Settings as _Settings


def Settings(**overrides):
    """Settings with a placeholder endpoint; the provider URL has no default."""
    return _Settings(**{"llm_base_url": "https://llm.example.invalid/v1", **overrides})
from orchestrator.llm import HttpLlmClient, ask, bind_run_counters, reset_run_counters
from orchestrator.models import RunStats
from orchestrator.run_store import RunCounters
from orchestrator.events import EventEmitter
from orchestrator.run_store import RunState
from orchestrator.ws import WebSocketHub
from tests.unit.api_support import METRICS


def response(cost=None, *, content="{}", finish_reason="stop", include_cost=True):
    usage = {"total_tokens": 7}
    if include_cost:
        usage["cost"] = cost
    # Deliberately allow malformed non-finite provider values in validation tests.
    return httpx.Response(200, content=json.dumps({
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": usage,
    }).encode(), headers={"Content-Type": "application/json"})


async def chat_responses(responses):
    stats = RunCounters()
    client = HttpLlmClient(Settings(llm_provider="openai_compatible", llm_api_key="test-key"),
                           transport=httpx.MockTransport(lambda request: responses.pop(0)))
    binding = bind_run_counters(stats)
    results = []
    try:
        while responses:
            results.append(await client.chat("planner", "system", "test input"))
    finally:
        reset_run_counters(binding)
        await client.close()
    return stats, results


async def test_cost_sums_all_reported_responses_and_reaches_summary_stats():
    stats, _ = await chat_responses([response(.0123), response(.0456), response(.01)])
    assert stats.cost_usd == Decimal("0.0679")
    assert stats.snapshot()["cost_usd"] == "0.0679"
    assert stats.llm_calls == 3 and stats.tokens_total == 21


async def test_partial_cost_is_never_reported_as_total():
    stats, _ = await chat_responses([response(.0123), response(include_cost=False)])
    assert stats.snapshot()["cost_usd"] is None


async def test_cost_counts_empty_answer_and_automatic_length_retry():
    stats, results = await chat_responses([
        response(.02, content=None, finish_reason="length"), response(.03),
        response(.04, content="", finish_reason="stop"),
    ])
    assert stats.cost_usd == Decimal("0.09")
    assert stats.llm_calls == 3
    assert len(results) == 2 and isinstance(results[-1], ParseFailure)


@pytest.mark.parametrize("cost", [None, True, False, "0.01", -.1, float("nan"),
                                 float("inf"), -float("inf"), 10 ** 400, {}, []])
async def test_invalid_or_missing_cost_stays_unknown(cost):
    stats, _ = await chat_responses([response(cost)])
    assert stats.cost_usd is None and stats.snapshot()["cost_usd"] is None
    assert stats.tokens_total == 7 and stats.llm_calls == 1


async def test_zero_cost_is_known_and_run_accounting_is_isolated():
    first, _ = await chat_responses([response(.7)])
    second, _ = await chat_responses([response(0)])
    third, _ = await chat_responses([response(include_cost=False)])
    assert first.cost_usd == Decimal("0.7")
    assert second.cost_usd == 0.0
    assert third.cost_usd is None


async def test_retry_without_provider_usage_does_not_estimate_cost():
    stats, _ = await chat_responses([httpx.Response(503), response(.125)])
    assert stats.cost_usd == .125 and stats.llm_calls == 2


async def test_summary_event_serializes_cost_and_old_missing_cost_is_null():
    run = RunState("run_cost", "test input")
    emitter = EventEmitter(WebSocketHub())
    await emitter.emit(run, "run_started", "intake", {"request": run.request})
    await emitter.emit(run, "validation_done", "validation", {"dataset": "validation", "metrics": METRICS})
    run.stats.cost_usd = Decimal("0.031")
    event = await emitter.emit(run, "summary", "approval", {"text": "Done.", "stats": run.stats.snapshot()})
    assert event["data"]["stats"]["cost_usd"] == "0.031"
    assert json.loads(json.dumps(event))["data"]["stats"]["cost_usd"] == "0.031"
    assert RunCounters().snapshot()["cost_usd"] is None


def test_sync_compatibility_client_also_counts_cost(monkeypatch):
    settings = Settings(llm_provider="openai_compatible", llm_api_key="test-key")
    monkeypatch.setattr(_Settings, "from_env", lambda **kwargs: settings)
    monkeypatch.setattr("orchestrator.llm.requests.post", lambda *args, **kwargs: response(.025))
    stats = RunCounters()
    binding = bind_run_counters(stats)
    try:
        assert ask("test input") == "{}"
    finally:
        reset_run_counters(binding)
    assert stats.cost_usd == Decimal("0.025") and stats.llm_calls == 1


@pytest.mark.parametrize("cost", [-1, float("nan"), float("inf")])
def test_summary_schema_rejects_invalid_cost(cost):
    with pytest.raises(ValidationError):
        RunStats(duration_ms=0, llm_calls=0, tokens_total=None, skills_built=0,
                 skills_reused=0, cost_usd=cost)


def test_legacy_stats_without_cost_remain_valid():
    value = RunStats(duration_ms=0, llm_calls=0, tokens_total=None,
                     skills_built=0, skills_reused=0)
    assert value.model_dump()["cost_usd"] is None


def test_prompt_versions_are_content_addressed():
    from orchestrator.llm import prompt_for, prompt_version, prompt_versions

    versions = prompt_versions()
    assert set(versions) == {"examiner", "forge", "planner", "rule_author", "summary"}
    assert versions["planner"] == prompt_version("planner", prompt_for("planner"))
    assert prompt_version("planner", prompt_for("planner") + " ") != versions["planner"]
    assert prompt_version("planner", "") is None
