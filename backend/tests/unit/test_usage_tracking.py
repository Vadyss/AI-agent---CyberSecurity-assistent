# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from decimal import Decimal
import json

import httpx
import pytest

from orchestrator.config import Settings
from orchestrator.llm import HttpLlmClient, LlmBudgetExceeded, LlmUsage, bind_run_counters, json_chat, reset_run_counters
from orchestrator.run_store import RunCounters
from orchestrator.usage import UsageLedger, calculate_cost, response_usage

RATES = {"test": {"input_per_million": "2", "cached_input_per_million": "0.2", "output_per_million": "10", "currency": "USD"}}


def test_decimal_cost_subtracts_cached_input_without_double_counting():
    assert calculate_cost("test", 1000, 200, 100, rates=RATES) == Decimal("0.00264")
    assert calculate_cost("test", 0, 0, 0, rates=RATES) == Decimal(0)
    assert calculate_cost("missing", 10, 0, 2, rates=RATES) is None
    assert calculate_cost("missing", 0, 0, 0, rates=RATES) is None
    assert calculate_cost("deepseek/deepseek-v4.1-flash", 0, 0, 0) is None
    assert calculate_cost("test", 1000, None, 100, rates=RATES) is None
    assert calculate_cost("test", 10, 20, 100, rates=RATES) is None


def test_real_usage_is_never_replaced_with_estimates():
    actual, cost = response_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 25,
        "prompt_tokens_details": {"cached_tokens": 30}, "cost": Decimal("0.000123456789123456789")}},
        "very long system " * 100, "very long user " * 100, succeeded=True)
    assert actual == dict(input_tokens=100, cached_tokens=30, output_tokens=25,
                         cache_write_tokens=None, total_tokens=125, estimated=False)
    assert cost == Decimal("0.000123456789123456789")
    partial, _ = response_usage({"usage": {"total_tokens": 500}}, "system", "user", succeeded=True)
    assert partial["total_tokens"] == 500 and partial["input_tokens"] is None and not partial["estimated"]


def test_missing_usage_is_explicit_offline_tokenizer_estimate():
    usage, cost = response_usage({"choices": [{"message": {"content": "ok", "reasoning": "hidden"}}]}, "s", "u", succeeded=True)
    assert usage["estimated"] is True and usage["input_tokens"] == 2 and usage["output_tokens"] == 8
    assert usage["total_tokens"] == 10 and cost is None
    failed, _ = response_usage(None, "s", "u", succeeded=False)
    assert failed["output_tokens"] is None and failed["total_tokens"] is None


def record(step, cost, retry=False, tokens=100):
    return dict(step=step, cost_usd=cost, retry=retry, total_tokens=tokens, estimated=False, cost_source="provider")


def test_step_aggregation_shares_retries_average_and_saved_json(tmp_path):
    first = UsageLedger("run_first", tmp_path)
    first.append(record("planner", "0.01")); first.finish()
    second = UsageLedger("run_second", tmp_path)
    second.append(record("planner", "0.01"))
    second.append(record("forge", "0.02", True))
    second.append(record("forge", "0.01"))
    summary = second.finish()["summary"]
    assert summary["totals"]["cost_usd"] == "0.04"
    assert summary["totals"]["total_tokens"] == 300
    assert summary["totals"]["steps"] == [
        {"step": "planner", "calls": 1, "retries": 0, "cost_usd": "0.01", "share_percent": "25.0"},
        {"step": "forge", "calls": 2, "retries": 1, "cost_usd": "0.03", "share_percent": "75.0"}]
    assert summary["most_expensive_step"] == "forge"
    assert summary["average_cost_usd"] == "0.025" and summary["average_run_count"] == 2
    assert "50.0%" in summary["observations"][1]
    saved = json.loads(second.path.read_text())
    assert saved["summary"] == summary and len(saved["records"]) == 3
    assert [r["call_id"] for r in saved["records"]] == [1, 2, 3]


def test_unknown_cost_is_not_zero_or_a_complete_subtotal(tmp_path):
    ledger = UsageLedger("run_unknown", tmp_path)
    ledger.append(record("planner", "0.01"))
    ledger.append(record("forge", None, True, None))
    result = ledger.finish()["summary"]
    assert result["totals"]["cost_usd"] is None
    assert result["totals"]["known_cost_usd"] == "0.01" and result["totals"]["unknown_cost_calls"] == 1
    assert result["totals"]["total_tokens"] is None and result["totals"]["known_tokens"] == 100
    assert result["most_expensive_step"] is None and result["average_run_count"] == 0
    assert all(s["share_percent"] is None for s in result["totals"]["steps"])


FORGE_ANSWER = '{"manifest": {}, "code": "", "tests": ""}'


async def test_every_attempt_emits_usage_even_on_retry_parse_repair_and_length(tmp_path, caplog):
    responses = [httpx.Response(503), httpx.Response(200, json={"choices": [{"message": {"content": None}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30, "cost": .001}}),
        httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}], "usage": {"total_tokens": 40, "cost": .002}}),
        httpx.Response(200, json={"choices": [{"message": {"content": FORGE_ANSWER}}], "usage": {"total_tokens": 50, "cost": .003}})]
    emitted = []
    async def publish(data): emitted.append(data)
    async def sleep(_): pass
    ledger = UsageLedger("run_tracked", tmp_path, publish)
    counters = RunCounters()
    binding = bind_run_counters(counters, "run_tracked", usage=ledger)
    client = HttpLlmClient(Settings(llm_api_key="secret-token", llm_provider="openai_compatible"),
        transport=httpx.MockTransport(lambda _: responses.pop(0)), sleep=sleep)
    try:
        assert await json_chat(client, "forge", "secret-system", "secret-prompt", {"attempt": 2}) == json.loads(FORGE_ANSWER)
    finally:
        await client.close(); reset_run_counters(binding)
    assert len(emitted) == 4 and counters.llm_calls == 4
    assert [r["attempt"] for r in ledger.records] == [1, 2, 3, 1]
    assert all(r["iteration"] == 2 and r["retry"] for r in ledger.records)
    assert counters.tokens_total == 120 and counters.cost_usd == Decimal("0.006")
    assert emitted[-1]["totals"]["known_cost_usd"] == "0.006"
    assert ledger.records[0]["status"] == "error" and ledger.records[0]["cost_usd"] is None
    text = ledger.path.read_text() + caplog.text
    assert all(secret not in text for secret in ("secret-token", "secret-system", "secret-prompt", "not json"))


async def test_mock_budget_failure_does_not_create_a_phantom_call():
    from orchestrator.llm_mock import MockLlm
    client = MockLlm(Settings(llm_provider="mock", llm_max_calls_per_run=1))
    counters = RunCounters(llm_calls=1)
    binding = bind_run_counters(counters)
    ledger = LlmUsage.get()
    try:
        with pytest.raises(LlmBudgetExceeded):
            await client.chat("planner", "s", "u")
        assert not ledger.records
    finally:
        reset_run_counters(binding)


def test_usage_storage_refuses_traversal_and_symlinks(tmp_path):
    from gatekeeper.api import UsageStore
    with pytest.raises(ValueError):
        UsageStore(tmp_path, "../../escape")
    root = tmp_path / "data"
    root.mkdir()
    (root / "runs").symlink_to(tmp_path / "outside")
    store = UsageStore(root, "run_safe")
    with pytest.raises(ValueError):
        store.save({"run_id": "run_safe", "records": [], "summary": None})
    assert not (tmp_path / "outside").exists()


def test_usage_storage_is_atomic_private_and_keeps_other_files(tmp_path):
    from gatekeeper.api import UsageStore
    store = UsageStore(tmp_path, "run_safe")
    store.save({"run_id": "run_safe", "records": [], "summary": None})
    original = store.path.read_text()
    assert store.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        store.save({"run_id": "run_other", "records": [], "summary": None})
    assert store.path.read_text() == original
    assert not list(store.path.parent.glob("*.tmp"))


@pytest.mark.parametrize("failure", [httpx.ReadTimeout("secret-timeout"), httpx.ConnectError("secret-connect")])
async def test_transport_failures_are_recorded_and_do_not_invent_output_or_cost(failure, caplog):
    def transport(_): raise failure
    client = HttpLlmClient(Settings(llm_api_key="secret-token", llm_provider="openai_compatible", llm_max_retries=0),
                          transport=httpx.MockTransport(transport))
    from orchestrator.llm import LlmError
    try:
        with pytest.raises(LlmError):
            await client.chat("planner", "secret-system", "secret-user")
        record = client.usage.records[0]
        assert record["estimated"] and record["output_tokens"] is None
        assert record["cost_usd"] is None and record["status"] == "error"
        assert record["attempt"] == 1
        assert not any(secret in json.dumps(record) + caplog.text for secret in ("secret-token", "secret-system", "secret-user", "secret-timeout", "secret-connect"))
    finally:
        await client.close()


async def test_usage_missing_uses_verified_pricing_only_on_the_verified_access_path():
    data = {"choices": [{"message": {"content": "ok"}}]}
    for settings, expected_source in [
        (Settings(apify_token="test-token"), "pricing"),
        (Settings(llm_provider="openai_compatible", llm_api_key="test-token", llm_base_url="https://other-provider.invalid/v1"), "unknown")]:
        client = HttpLlmClient(settings, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=data)))
        try:
            await client.chat("planner", "s", "u")
            record = client.usage.records[0]
            assert record["estimated"] and record["cost_source"] == expected_source
            assert (record["cost_usd"] is not None) == (expected_source == "pricing")
        finally:
            await client.close()


async def test_examiner_repairs_have_distinct_iterations_and_report_actual_model():
    from examiner import Examiner
    replies = ["not-json", '{"code":"def generate(seed): return {}"}']
    client = HttpLlmClient(Settings(apify_token="test-token"), transport=httpx.MockTransport(lambda _: httpx.Response(200,
        json={"model": "deepseek/deepseek-v4.1-flash", "choices": [{"message": {"content": replies.pop(0)}}],
              "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20, "cost": .001}})))
    try:
        assert isinstance(await Examiner(client).generate("custom attack", "log format"), dict)
        assert [r["iteration"] for r in client.usage.records] == [1, 2]
        assert [r["retry"] for r in client.usage.records] == [False, True]
        assert all(r["model"] == "deepseek/deepseek-v4.1-flash" for r in client.usage.records)
    finally:
        await client.close()


def test_no_call_run_has_exact_zero_totals_and_is_included_in_average(tmp_path):
    summary = UsageLedger("run_empty", tmp_path).finish()["summary"]
    assert summary["totals"]["total_tokens"] == 0
    assert summary["totals"]["cost_usd"] == "0"
    assert summary["most_expensive_step"] is None
    assert summary["average_run_count"] == 1 and summary["average_cost_usd"] == "0"
    assert summary["observations"] == ["No model calls were made."]


def test_legacy_sync_call_publishes_usage_even_with_no_running_loop(monkeypatch):
    from orchestrator.llm import ask
    emitted = []
    async def publish(event): emitted.append(event)
    ledger = UsageLedger("run_legacy", publish=publish)
    settings = Settings(llm_provider="openai_compatible", llm_api_key="test-key")
    monkeypatch.setattr(Settings, "from_env", lambda **kwargs: settings)
    monkeypatch.setattr("orchestrator.llm.requests.post", lambda *args, **kwargs: httpx.Response(200,
        json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 15, "cost": .001}}))
    binding = bind_run_counters(RunCounters(), "run_legacy", usage=ledger)
    try:
        assert ask("test input") == "ok"
    finally:
        reset_run_counters(binding)
    assert len(emitted) == 1 and emitted[0]["record"]["step"] == "legacy"
    assert emitted[0]["record"]["cost_usd"] == "0.001"


def test_untrusted_money_exponents_cannot_expand_into_unbounded_strings():
    from orchestrator.usage import money
    assert money(Decimal("1e-999999999")) is None
    assert money(Decimal("1e999999999")) is None
    assert money(Decimal("0e-999999999")) == Decimal(0)
    assert money(Decimal("0.01234567890123456789")) == Decimal("0.01234567890123456789")
