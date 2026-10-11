# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import json
import time
from decimal import Decimal

import httpx

from orchestrator import llm
from orchestrator.usage import UsageLedger


def _response(cost):
    body = {"model": "deepseek/deepseek-v4.1-flash", "choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7, "cost": cost}}
    return httpx.Response(200, content=json.dumps(body).encode())


def test_provider_cost_for_provider_reported_model_has_no_warning():
    event = llm._capture_usage(UsageLedger("run_abcd"), "examiner", "deepseek/deepseek-v4.1-flash", "s", "u",
                               _response(0.0001), time.monotonic(), 1)
    record = event["record"]
    assert record["cost_source"] == "provider" and record["cost_usd"] == "0.0001" and record["warning"] is None


def test_missing_provider_cost_for_provider_reported_model_stays_unknown():
    event = llm._capture_usage(UsageLedger("run_abcd"), "examiner", "deepseek/deepseek-v4.1-flash", "s", "u",
                               _response(None), time.monotonic(), 1)
    assert event["record"]["cost_usd"] is None and event["record"]["warning"]


async def test_publish_failure_never_replaces_result():
    ledger = UsageLedger("run_abcd")

    async def broken(_event):
        raise RuntimeError("boom")
    ledger.publish = broken

    async def post(*_a, **_k):
        return _response(0.01)
    client = type("C", (), {"post": staticmethod(post)})()
    response = await llm._tracked_post(client, "http://x", ledger=ledger, role="planner", model="m", system="s",
                                       user="u", attempt=1, context=None, pricing_allowed=False)
    assert response.status_code == 200


def _saved(root, name, cost, source):
    path = root / "runs" / name / "usage.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"records": [{"cost_source": source}],
                                "summary": {"totals": {"unknown_cost_calls": 0, "cost_usd": cost}}}))


def test_average_skips_mock_runs_and_reads_only_last_50(tmp_path):
    _saved(tmp_path, "run_mock1", "0", "mock")
    for index in range(55):
        _saved(tmp_path, f"run_real{index:02d}", "1", "provider")
    ledger = UsageLedger("run_now1", tmp_path)
    ledger.records = [{"step": "planner", "cost_usd": "2", "retry": False, "total_tokens": 1, "estimated": False,
                       "cost_source": "provider"}]
    summary = ledger.finish()["summary"]
    assert summary["average_run_count"] == 51
    assert Decimal(summary["average_cost_usd"]) == Decimal("51") / 51 + Decimal("1") / 51
