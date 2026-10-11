# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Content-free usage ledger. Monetary arithmetic never uses binary floats."""
from __future__ import annotations

import json
import asyncio
import logging
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path

from gatekeeper.api import UsageStore

logger = logging.getLogger(__name__)
MAX_AVERAGE_RUNS = 50


def money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            return None
        if number == 0:
            return Decimal(0)
        # Bound untrusted exponents before fixed-point serialization can allocate
        # an enormous string. Normal provider currency precision fits easily.
        if number.as_tuple().exponent < -100 or number.adjusted() > 308 or len(number.as_tuple().digits) > 100:
            return None
        return number
    except InvalidOperation:
        return None


def decimal_text(value):
    return None if value is None else format(value, "f")


def token_count(value):
    return value if type(value) is int and value >= 0 else None


@lru_cache(maxsize=1)
def prices():
    return json.loads(Path(__file__).with_name("pricing.json").read_text())["models"]


def calculate_cost(model, input_tokens, cached_tokens, output_tokens, *, cache_write_tokens=0, rates=None):
    rates = (prices() if rates is None else rates).get(model, {})
    if rates.get("currency") != "USD" or money(rates.get("input_per_million")) is None or money(rates.get("output_per_million")) is None:
        return None
    if None in (input_tokens, cached_tokens, output_tokens, cache_write_tokens):
        return None
    if min(input_tokens, cached_tokens, output_tokens, cache_write_tokens) < 0 or cached_tokens + cache_write_tokens > input_tokens:
        return None
    amounts = (input_tokens - cached_tokens - cache_write_tokens, cached_tokens, output_tokens, cache_write_tokens)
    keys = ("input_per_million", "cached_input_per_million", "output_per_million", "cache_write_per_million")
    total = Decimal(0)
    for amount, key in zip(amounts, keys):
        if not amount:
            continue
        rate = money(rates.get(key))
        if rate is None or rates.get("currency") != "USD":
            return None
        total += Decimal(amount) * rate / Decimal(1_000_000)
    return total


def estimate_tokens(text):
    # An offline byte-level tokenizer: one UTF-8 byte per token. Conservative,
    # model-independent, and explicitly estimated; no downloads or prompt storage.
    return len(text.encode("utf-8", errors="replace"))


def response_usage(data, system, user, *, succeeded):
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("prompt_tokens_details", usage.get("input_tokens_details", {}))
    details = details if isinstance(details, dict) else {}
    input_tokens = token_count(usage.get("prompt_tokens", usage.get("input_tokens")))
    output_tokens = token_count(usage.get("completion_tokens", usage.get("output_tokens")))
    cached = token_count(details.get("cached_tokens", usage.get("cache_read_input_tokens", usage.get("prompt_cache_hit_tokens"))))
    cache_write = token_count(details.get("cache_write_tokens", usage.get("cache_creation_input_tokens")))
    total = token_count(usage.get("total_tokens"))
    estimated = input_tokens is None and output_tokens is None and total is None
    if estimated:
        input_tokens = estimate_tokens(system) + estimate_tokens(user)
        output_tokens = None
        if succeeded and isinstance(data, dict):
            choices = data.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                message = choices[0].get("message")
                if isinstance(message, dict):
                    output_tokens = sum(estimate_tokens(message.get(key)) for key in ("content", "reasoning", "reasoning_content")
                                        if isinstance(message.get(key), str))
        cached = 0
        cache_write = 0
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    raw_cost = usage.get("cost")
    reported = money(raw_cost) if type(raw_cost) in (int, float, Decimal) else None
    if reported is not None and reported > Decimal("1e308"):
        reported = None
    return dict(input_tokens=input_tokens, cached_tokens=cached, output_tokens=output_tokens,
                cache_write_tokens=cache_write, total_tokens=total, estimated=estimated), reported


class UsageLedger:
    def __init__(self, run_id="unbound", data_dir=None, publish=None):
        self.run_id = run_id
        self.records = []
        self.publish = publish
        self.pending = []
        try:
            self.loop = asyncio.get_running_loop() if publish is not None else None
        except RuntimeError:
            self.loop = None
        self.summary = None
        self.store = UsageStore(data_dir, run_id) if data_dir is not None else None
        self.path = self.store.path if self.store else None

    def append(self, record):
        record = {**record, "run_id": self.run_id, "call_id": len(self.records) + 1}
        self.records.append(record)
        self.save()
        return {"kind": "call", "record": record, "totals": self.aggregate(), "summary": None}

    def aggregate(self):
        steps = []
        known = sum((money(r["cost_usd"]) or Decimal(0) for r in self.records), Decimal(0))
        unknown = sum(r["cost_usd"] is None for r in self.records)
        total_cost = None if unknown else known
        for step in dict.fromkeys(r["step"] for r in self.records):
            records = [r for r in self.records if r["step"] == step]
            cost = None if any(r["cost_usd"] is None for r in records) else sum((money(r["cost_usd"]) for r in records), Decimal(0))
            steps.append({"step": step, "calls": len(records), "retries": sum(r["retry"] for r in records),
                          "cost_usd": decimal_text(cost),
                          "share_percent": decimal_text((cost * 100 / total_cost).quantize(Decimal("0.1"))) if cost is not None and total_cost else None})
        complete = all(r["total_tokens"] is not None for r in self.records)
        return {"calls": len(self.records), "total_tokens": sum(r["total_tokens"] for r in self.records) if complete else None,
                "known_tokens": sum(r["total_tokens"] or 0 for r in self.records),
                "cost_usd": decimal_text(total_cost), "known_cost_usd": decimal_text(known), "unknown_cost_calls": unknown,
                "estimated_calls": sum(r["estimated"] for r in self.records),
                "calculated_cost_calls": sum(r["cost_source"] == "pricing" for r in self.records),
                "retries": sum(r["retry"] for r in self.records), "steps": steps}

    def finish(self):
        totals = self.aggregate()
        ranked = [s for s in totals["steps"] if s["cost_usd"] is not None]
        most = max(ranked, key=lambda s: money(s["cost_usd"]), default=None) if not totals["unknown_cost_calls"] and money(totals["cost_usd"]) else None
        costs = []
        if self.path and self.path.parent.parent.exists():
            def modified(path):
                try:
                    return path.stat().st_mtime
                except OSError:
                    return 0
            recent = sorted((p for p in self.path.parent.parent.glob("run_*/usage.json") if p != self.path),
                            key=modified, reverse=True)[:MAX_AVERAGE_RUNS]
            for path in recent:
                try:
                    data = json.loads(path.read_text())
                    previous = data.get("summary")
                    if any(r.get("cost_source") == "mock" for r in data.get("records", [])):
                        continue
                    if previous and not previous["totals"]["unknown_cost_calls"]:
                        costs.append(money(previous["totals"]["cost_usd"]))
                except (OSError, ValueError, KeyError, TypeError):
                    logger.warning("A saved usage summary could not be read.")
        if totals["cost_usd"] is not None and not any(r["cost_source"] == "mock" for r in self.records):
            costs.append(money(totals["cost_usd"]))
        costs = [c for c in costs if c is not None]
        observations = []
        if most:
            observations.append(f"{most['step'].replace('_', ' ').capitalize()} used the most money ({most['share_percent'] or '0'}% of the total).")
        if totals["unknown_cost_calls"]:
            observations.append(f"Cost is unknown for {totals['unknown_cost_calls']} calls; the known subtotal is not the full cost.")
        elif totals["retries"]:
            retry_cost = sum((money(r["cost_usd"]) for r in self.records if r["retry"]), Decimal(0))
            share = retry_cost * 100 / money(totals["cost_usd"]) if money(totals["cost_usd"]) else Decimal(0)
            observations.append(f"Retries account for {share:.1f}% of cost ({totals['retries']} calls).")
        if totals["estimated_calls"]:
            observations.append(f"Token usage was estimated for {totals['estimated_calls']} calls.")
        if not observations:
            observations.append("No model calls were made." if not totals["calls"] else "No retries were needed.")
        self.summary = {"totals": totals, "most_expensive_step": most["step"] if most else None,
                        "average_cost_usd": decimal_text(sum(costs, Decimal(0)) / len(costs)) if costs else None,
                        "average_run_count": len(costs), "observations": observations[:3]}
        self.save()
        return {"kind": "summary", "record": None, "totals": totals, "summary": self.summary}

    def save(self):
        if self.path is None:
            return
        try:
            self.store.save({"run_id": self.run_id, "records": self.records, "summary": self.summary})
        except (OSError, ValueError):
            logger.warning("Usage file could not be saved run_id=%s", self.run_id)
