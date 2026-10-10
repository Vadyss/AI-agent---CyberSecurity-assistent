# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""LLM transport, bounded retries, per-run accounting and JSON extraction."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from contextvars import ContextVar, Token
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache, wraps
from html import escape
from pathlib import Path
from typing import Any, Protocol

from .usage import UsageLedger, calculate_cost, decimal_text, money, response_usage

import httpx
import requests

from gatekeeper.types import ParseFailure

BASE_URL = "https://piquant-peacoat--llm-relay.apify.actor/v1"
MODEL = "anthropic/claude-sonnet-5.5"
logger = logging.getLogger(__name__)
LlmBudget: ContextVar[Any | None] = ContextVar("llm_run_counters", default=None)
LlmUsage: ContextVar[UsageLedger | None] = ContextVar("llm_usage", default=None)
UsageIteration: ContextVar[int] = ContextVar("llm_usage_iteration", default=1)


@dataclass
class ProviderState:
    failures: int = 0
    fallback: bool = False
    run_id: str = "unbound"


LlmProviderState: ContextVar[ProviderState | None] = ContextVar("llm_provider_state", default=None)


@dataclass(frozen=True)
class RunBinding:
    counters: Token
    provider: Token
    usage: Token


class LlmError(Exception):
    """Safe provider error: never includes HTTP bodies, keys or prompts."""


class LlmBudgetExceeded(LlmError):
    pass


class TenantBudgetExceeded(LlmBudgetExceeded):
    """The tenant's monthly model budget is used up."""


@dataclass
class TenantBudget:
    remaining_usd: Decimal


# Bound per run by the dispatcher; None outside runs (scripts, tests).
TenantSpend: ContextVar[TenantBudget | None] = ContextVar("tenant_budget", default=None)


@dataclass(frozen=True)
class LlmResult:
    text: str
    total_tokens: int | None
    model: str


class LlmClient(Protocol):
    async def chat(self, role: str, system: str, user: str, *, max_tokens: int | None = None,
                   temperature: float = 0.2, context: dict | None = None) -> LlmResult | ParseFailure: ...
    async def close(self) -> None: ...


def bind_run_counters(counters: Any, run_id: str = "unbound", *, usage: UsageLedger | None = None) -> RunBinding:
    return RunBinding(LlmBudget.set(counters), LlmProviderState.set(ProviderState(run_id=run_id)),
                      LlmUsage.set(usage or UsageLedger(run_id)))


def reset_run_counters(token: RunBinding) -> None:
    LlmBudget.reset(token.counters)
    LlmProviderState.reset(token.provider)
    LlmUsage.reset(token.usage)


def count_call(max_calls: int) -> None:
    budget = TenantSpend.get()
    if budget is not None and budget.remaining_usd <= 0:
        raise TenantBudgetExceeded("The tenant's monthly model budget is used up.")
    counters = LlmBudget.get()
    if counters is not None:
        if counters.llm_calls >= max_calls:
            raise LlmBudgetExceeded("The language model call budget was exceeded.")
        counters.llm_calls += 1


def count_tokens(tokens: int | None) -> None:
    counters = LlmBudget.get()
    if counters is not None and tokens is not None:
        counters.tokens_total = (counters.tokens_total or 0) + tokens


def count_cost(cost) -> None:
    budget = TenantSpend.get()
    if budget is not None and cost is not None:
        budget.remaining_usd -= cost
    counters = LlmBudget.get()
    if counters is None:
        return
    if cost is None:
        counters.cost_unknown = True
    else:
        counters.cost_usd = (money(getattr(counters, "cost_usd", None)) or money(0)) + cost


def _capture_usage(ledger, role, model, system, user, response, started, attempt, context=None, *, mock_text=None, pricing_allowed=True):
    data = None
    if response is not None and len(response.content) <= 2_000_000:
        try:
            data = json.loads(response.content, parse_float=money)
        except (ValueError, UnicodeError):
            pass
    if mock_text is not None:
        data = {"choices": [{"message": {"content": mock_text}}]}
    if isinstance(data, dict) and isinstance(data.get("model"), str) and data["model"].strip():
        model = data["model"]
    succeeded = mock_text is not None or response is not None and 200 <= response.status_code < 300
    usage, reported_cost = response_usage(data, system, user, succeeded=succeeded)
    context = context or {}
    iteration = context.get("attempt", UsageIteration.get())
    cost = reported_cost
    source = "provider" if cost is not None else "unknown"
    if model == "mock" and mock_text is not None:
        cost, source = money(0), "mock"
    elif cost is None and succeeded and pricing_allowed:
        cost = calculate_cost(model, usage["input_tokens"], usage["cached_tokens"], usage["output_tokens"],
                              cache_write_tokens=usage["cache_write_tokens"])
        if cost is not None:
            source = "pricing"
    # Existing summary.stats retains provider-only token semantics. The usage
    # event includes estimates separately and never overwrites real counts.
    real_tokens = _usage_tokens(data)
    if real_tokens is None and not usage["estimated"]:
        real_tokens = usage["total_tokens"]
    count_tokens(real_tokens)
    count_cost(cost)
    warning = None
    if cost is None:
        warning = "Cost is unknown: provider cost or verified pricing is unavailable."
    elif source == "pricing":
        warning = "Cost is calculated from published rates, not a provider charge."
    record = dict(step=role, iteration=iteration, attempt=attempt, model=model,
                  **usage, cost_usd=decimal_text(cost), cost_source=source, currency="USD",
                  duration_ms=max(0, int((time.monotonic() - started) * 1000)),
                  timestamp=datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                  retry=attempt > 1 or iteration > 1 or bool(context.get("repair")),
                  status="success" if succeeded else "error", warning=warning)
    if warning:
        logger.warning("LLM usage run_id=%s step=%s model=%s: %s", ledger.run_id, role, model, warning)
    return ledger.append(record)


def _priced_access(settings):
    return settings.llm_provider == "apify" and settings.llm_base_url.rstrip("/") == BASE_URL


async def _safe_publish(ledger, event):
    try:
        await ledger.publish(event)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("LLM usage event could not be published (%s).", type(exc).__name__)


async def _tracked_post(http, url, *, ledger, role, model, system, user, attempt, context, pricing_allowed, **kwargs):
    response = None
    started = time.monotonic()
    try:
        response = await http.post(url, **kwargs)
        return response
    finally:
        event = _capture_usage(ledger, role, model, system, user, response, started, attempt, context, pricing_allowed=pricing_allowed)
        if ledger.publish:
            await _safe_publish(ledger, event)


def _tracked_sync_post(url, *, ledger, model, system, user, attempt, pricing_allowed, **kwargs):
    response = None
    started = time.monotonic()
    try:
        response = requests.post(url, **kwargs)
        return response
    finally:
        event = _capture_usage(ledger, "legacy", model, system, user, response, started, attempt, pricing_allowed=pricing_allowed)
        if ledger.publish:
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None and (ledger.loop is None or loop is ledger.loop):
                    ledger.loop = loop
                    ledger.pending.append(loop.create_task(_safe_publish(ledger, event)))
                elif ledger.loop is not None:
                    asyncio.run_coroutine_threadsafe(_safe_publish(ledger, event), ledger.loop).result()
                else:
                    asyncio.run(_safe_publish(ledger, event))
            except Exception as exc:
                logger.warning("LLM usage event could not be published (%s).", type(exc).__name__)


def track_mock_call(method):
    """Mocks share the central ledger without changing their proposals."""
    @wraps(method)
    async def tracked(self, role, system, user, **kwargs):
        started = time.monotonic()
        result = None
        counters = LlmBudget.get()
        previous_calls = counters.llm_calls if counters is not None else None
        try:
            result = await method(self, role, system, user, **kwargs)
            return result
        finally:
            if counters is None or counters.llm_calls != previous_calls:
                ledger = LlmUsage.get() or self.usage
                event = _capture_usage(ledger, role, "mock", system, user, None, started, 1,
                                       kwargs.get("context"), mock_text=result.text if isinstance(result, LlmResult) else None)
                if ledger.publish:
                    await _safe_publish(ledger, event)
    return tracked


def _strict_json(text: str):
    def reject_constant(value):
        raise ValueError("JSON does not support nonfinite numbers")
    return json.loads(text, parse_constant=reject_constant)


def extract_json(text: str) -> dict | ParseFailure:
    if not isinstance(text, str):
        return ParseFailure("The response is not a string.")
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:-3].strip()
    try:
        value = _strict_json(text)
    except (ValueError, TypeError, RecursionError):
        start = text.find("{")
        if start == -1:
            return ParseFailure("The response does not contain a JSON object.")
        depth = 0
        quoted = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = _strict_json(text[start:index + 1])
                    except (ValueError, RecursionError):
                        return ParseFailure("The response contains invalid JSON.")
                    return value if isinstance(value, dict) else ParseFailure("The output must be a JSON object.")
        return ParseFailure("The JSON object is incomplete.")
    return value if isinstance(value, dict) else ParseFailure("The output must be a JSON object.")


def _usage_tokens(data: Any) -> int | None:
    usage = data.get("usage") if isinstance(data, dict) else None
    tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    return tokens if type(tokens) is int and tokens >= 0 else None


def _result(data: dict, model: str) -> LlmResult | ParseFailure:
    try:
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            return ParseFailure("The language model did not return a nonempty text response.")
        try:
            content.encode("utf-8")
        except UnicodeError:
            return ParseFailure("The language model response is not valid UTF-8 text.")
        return LlmResult(content, _usage_tokens(data), model)
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise LlmError("The provider returned an invalid response.") from None


def _provider_failed(settings, state: ProviderState, model: str) -> None:
    if _primary_model(settings, model) and not state.fallback:
        state.failures += 1
        if state.failures >= 2 and settings.llm_model_fallback:
            state.fallback = True
            logger.warning("LLM fallback run_id=%s primary=%s fallback=%s provider_failures=%d",
                           state.run_id, settings.llm_model, settings.llm_model_fallback, state.failures)


def _provider_succeeded(settings, state: ProviderState, model: str) -> None:
    if _primary_model(settings, model) and not state.fallback:
        state.failures = 0


def _primary_model(settings, model: str) -> bool:
    return model in {settings.llm_model, settings.llm_model_planner, settings.llm_model_forge,
                     settings.llm_model_rule, settings.llm_model_summary}


def _response_data(response, settings, state: ProviderState, model: str) -> tuple[Any, LlmResult | ParseFailure]:
    if len(getattr(response, "content", b"")) > 2_000_000:
        raise LlmError("The language model response is too large.")
    try:
        data = response.json()
    except ValueError:
        raise LlmError("The provider returned an invalid response.") from None
    result = _result(data, model)
    _provider_succeeded(settings, state, model)
    return data, result


def _truncated(data: Any) -> bool:
    try:
        return data["choices"][0].get("finish_reason") == "length"
    except (KeyError, IndexError, TypeError, AttributeError):
        return False


def _reasoning(settings) -> dict:
    if settings.llm_reasoning_effort:
        return {"reasoning": {"enabled": True, "effort": settings.llm_reasoning_effort}}
    return {}


class HttpLlmClient:
    def __init__(self, settings, *, transport: httpx.AsyncBaseTransport | None = None, sleep=asyncio.sleep):
        self.settings = settings
        self._sleep = sleep
        self._http = httpx.AsyncClient(timeout=settings.llm_timeout_s, transport=transport,
                                       follow_redirects=False, trust_env=False)
        self._standalone_state = ProviderState()
        self.usage = UsageLedger()

    def _model(self, role: str) -> str:
        state = LlmProviderState.get() or self._standalone_state
        if state.fallback:
            return self.settings.llm_model_fallback
        suffix = {"rule_author": "rule", "summarizer": "summary", "summary": "summary"}.get(role, role)
        return getattr(self.settings, f"llm_model_{suffix}", self.settings.llm_model) or self.settings.llm_model

    async def chat(self, role: str, system: str, user: str, *, max_tokens: int | None = None,
                   temperature: float = 0.2, context: dict | None = None) -> LlmResult | ParseFailure:
        state = LlmProviderState.get() or self._standalone_state
        key = (self.settings.apify_token or self.settings.llm_api_key) if self.settings.llm_provider == "apify" else self.settings.llm_api_key
        if not key:
            raise LlmError("The language model API key is missing.")
        token_limit = min(max_tokens or self.settings.llm_max_tokens, self.settings.llm_max_tokens_cap)
        payload = {"messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "max_tokens": token_limit, "temperature": temperature, **_reasoning(self.settings)}
        started = time.monotonic()
        attempt = 0
        length_retried = False
        network_attempt = 0
        while True:
            model = self._model(role)
            payload["model"] = model
            count_call(self.settings.llm_max_calls_per_run)
            network_attempt += 1
            try:
                response = await _tracked_post(self._http, self.settings.llm_base_url.rstrip("/") + "/chat/completions",
                                                 ledger=LlmUsage.get() or self.usage, role=role, model=model, system=system, user=user,
                                                 attempt=network_attempt, context=context, pricing_allowed=_priced_access(self.settings), headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, json=payload)
                retry = response.status_code == 429 or response.status_code >= 500
                if not 200 <= response.status_code < 300:
                    logger.warning("LLM provider_error run_id=%s role=%s model=%s category=http status=%d",
                                   state.run_id, role, model, response.status_code)
                if retry:
                    _provider_failed(self.settings, state, model)
                else:
                    if not 200 <= response.status_code < 300:
                        raise LlmError(f"The language model request failed (HTTP {response.status_code}).")
                    try:
                        data, result = _response_data(response, self.settings, state, model)
                    except LlmError:
                        _provider_failed(self.settings, state, model)
                        raise
                    logger.info("LLM run_id=%s role=%s model=%s duration_ms=%d tokens=%s max_tokens=%d output=%s output_chars=%d truncated=%s",
                                state.run_id, role, model, int((time.monotonic() - started) * 1000),
                                _usage_tokens(data), payload["max_tokens"], "empty" if isinstance(result, ParseFailure) else "text",
                                0 if isinstance(result, ParseFailure) else len(result.text), _truncated(data))
                    if isinstance(result, ParseFailure) and _truncated(data) and not length_retried:
                        enlarged = min(payload["max_tokens"] * 2, self.settings.llm_max_tokens_cap)
                        payload["max_tokens"] = enlarged
                        length_retried = True
                        continue
                    return result
            except (httpx.TimeoutException, httpx.NetworkError):
                logger.warning("LLM provider_error run_id=%s role=%s model=%s category=timeout_or_network",
                               state.run_id, role, model)
                _provider_failed(self.settings, state, model)
                retry = True
            except httpx.HTTPError:
                logger.warning("LLM provider_error run_id=%s role=%s model=%s category=protocol",
                               state.run_id, role, model)
                _provider_failed(self.settings, state, model)
                raise LlmError("The language model request failed.") from None
            if attempt == self.settings.llm_max_retries:
                raise LlmError("The language model request failed after retries.") from None
            await self._sleep((1, 3)[min(attempt, 1)])
            attempt += 1

    async def close(self) -> None:
        await self._http.aclose()


def make_client(settings) -> LlmClient:
    if settings.llm_provider == "mock":
        from .llm_mock import MockLlm
        return MockLlm(settings)
    return HttpLlmClient(settings)


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def untrusted(name: str, value: Any) -> str:
    content = json.dumps(json_value(value), ensure_ascii=False, separators=(",", ":"))
    return f'<untrusted_data name="{name}">{escape(content, quote=False)}</untrusted_data>'


@lru_cache(maxsize=8)
def prompt_for(role: str) -> str:
    return (Path(__file__).resolve().parent / "prompts" / f"{role}.md").read_text(encoding="utf-8")


async def json_chat(client: LlmClient, role: str, system: str, user: str, context: dict) -> dict | ParseFailure:
    result = await client.chat(role, system, user, context=context)
    if isinstance(result, ParseFailure):
        return result
    parsed = extract_json(result.text)
    if isinstance(parsed, ParseFailure):
        logger.info("LLM json_parse_failed run_id=%s role=%s stage=initial",
                    (LlmProviderState.get() or ProviderState()).run_id, role)
        repair = dict(context, repair=True)
        repaired = await client.chat(role, system,
                                     user + "\n" + untrusted("invalid_output", result.text[:20000])
                                     + "\nReturn only valid JSON matching the required schema, with no additional text.", context=repair)
        parsed = repaired if isinstance(repaired, ParseFailure) else extract_json(repaired.text)
        if isinstance(parsed, ParseFailure):
            logger.info("LLM json_parse_failed run_id=%s role=%s stage=repair",
                        (LlmProviderState.get() or ProviderState()).run_id, role)
        return parsed
    return parsed


def ask(prompt: str, system: str | None = None, model: str | None = None) -> str | ParseFailure:
    """Original synchronous requests API retained for IDE scripts."""
    from orchestrator.config import Settings
    settings = Settings.from_env(load_env_file=True, llm_only=True)
    if settings.llm_provider == "mock":
        raise LlmError("Synchronous ask requires a live provider; the mock uses async chat.")
    key = (settings.apify_token or settings.llm_api_key) if settings.llm_provider == "apify" else settings.llm_api_key
    if not key:
        raise LlmError("The language model API key is missing.")
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    state = LlmProviderState.get() or ProviderState()
    primary = model or settings.llm_model
    token_limit = settings.llm_max_tokens
    length_retried = False
    attempt = 0
    network_attempt = 0
    ledger = LlmUsage.get() or UsageLedger()
    while True:
        model = settings.llm_model_fallback if state.fallback else primary
        count_call(settings.llm_max_calls_per_run)
        network_attempt += 1
        try:
            response = _tracked_sync_post(settings.llm_base_url.rstrip("/") + "/chat/completions",
                                     ledger=ledger, model=model, system=system or "", user=prompt, attempt=network_attempt, pricing_allowed=_priced_access(settings),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                     json={"model": model, "messages": messages, "max_tokens": token_limit, "temperature": 0.2,
                                           **_reasoning(settings)},
                                     timeout=settings.llm_timeout_s, allow_redirects=False)
            retry = response.status_code == 429 or response.status_code >= 500
            if retry:
                _provider_failed(settings, state, model)
            else:
                if not 200 <= response.status_code < 300:
                    raise LlmError(f"The language model request failed (HTTP {response.status_code}).")
                try:
                    data, result = _response_data(response, settings, state, model)
                except LlmError:
                    _provider_failed(settings, state, model)
                    raise
                if isinstance(result, ParseFailure) and _truncated(data) and not length_retried:
                    enlarged = min(token_limit * 2, settings.llm_max_tokens_cap)
                    token_limit = enlarged
                    length_retried = True
                    continue
                return result if isinstance(result, ParseFailure) else result.text
        except (requests.Timeout, requests.ConnectionError):
            _provider_failed(settings, state, model)
            retry = True
        except (requests.RequestException, ValueError):
            raise LlmError("The language model request failed.") from None
        if attempt == settings.llm_max_retries:
            raise LlmError("The language model request failed after retries.") from None
        time.sleep((1, 3)[min(attempt, 1)])
        attempt += 1
