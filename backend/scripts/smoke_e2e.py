#!/usr/bin/env python3
# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Real HTTP/WebSocket smoke, requiring a clean isolated mock demo registry."""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
import sys
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import connect

SEQUENCE_A = [
    "run_started", "plan_ready", "skill_reused", "capability_missing", "forge_started",
    "skill_tests_failed", "forge_started", "skill_candidate_ready", "rule_drafted",
    "rule_evaluated", "rule_drafted", "rule_evaluated", "validation_done", "summary", "awaiting_approval",
]
SEQUENCE_B = [
    "run_started", "plan_ready", "skill_reused", "skill_reused", "rule_drafted",
    "rule_evaluated", "validation_done", "summary", "awaiting_approval",
]


def core_types(events: list[dict]) -> list[str]:
    """Event types without llm_usage, whose count depends on model calls."""
    return [event["type"] for event in events if event["type"] != "llm_usage"]


async def smoke(base: str, timeout_s: float, idle_s: float = 0) -> None:
    parts = urlsplit(base.rstrip("/"))
    ws_url = urlunsplit(("wss" if parts.scheme == "https" else "ws", parts.netloc,
                        parts.path + "/api/ws", "", ""))
    observed: list[dict] = []
    async with httpx.AsyncClient(base_url=base.rstrip("/"), timeout=10, trust_env=False) as client:
        health = await client.get("/api/health")
        health.raise_for_status()
        assert health.json() == {"status": "ok", "contract_version": 1}, health.text
        skills = (await client.get("/api/skills")).json()["skills"]
        assert "distinct_count_window" not in {skill["name"] for skill in skills}, (
            "Smoke tests require a fresh demo registry. Use a separate Compose project; do not delete existing data.")
        async with connect(ws_url, open_timeout=10, ping_interval=20) as websocket:
            remaining = idle_s
            while remaining > 0:
                pause = min(30, remaining)
                await asyncio.sleep(pause)
                remaining -= pause
                pong = await websocket.ping()
                await asyncio.wait_for(pong, timeout=5)
                print(f"WebSocket idle: {idle_s - remaining:.0f} s, ping/pong OK.", flush=True)
            async def collect() -> None:
                async for raw in websocket:
                    observed.append(json.loads(raw))
            collector = asyncio.create_task(collect())
            try:
                for request, expected, suffix, stats in (
                    ("I want to detect password spraying on SSH.", SEQUENCE_A,
                     ["skill_installed", "rule_approved"], (1, 1)),
                    ("I want to detect distributed brute force on SSH.", SEQUENCE_B,
                     ["rule_approved"], (0, 2)),
                ):
                    created = await client.post("/api/runs", json={"request": request})
                    assert created.status_code == 202, created.text
                    run_id = created.json()["run_id"]
                    started = time.monotonic()
                    while True:
                        answer = await client.get(f"/api/runs/{run_id}/events")
                        answer.raise_for_status()
                        events = answer.json()["events"]
                        if events and events[-1]["type"] in {"awaiting_approval", "run_failed"}:
                            break
                        if time.monotonic() - started > timeout_s:
                            raise RuntimeError("The run exceeded the smoke test timeout.")
                        await asyncio.sleep(0.1)
                    assert core_types(events) == expected, events
                    summary = next(event["data"] for event in events if event["type"] == "summary")
                    assert (summary["stats"]["skills_built"], summary["stats"]["skills_reused"]) == stats
                    tokens = summary["stats"]["tokens_total"]
                    assert tokens is None or (type(tokens) is int and tokens >= 0), summary["stats"]
                    cost = summary["stats"]["cost_usd"]
                    assert cost is None or (isinstance(cost, str) and re.fullmatch(r"\d+(?:\.\d+)?", cost)), summary["stats"]
                    approved = await client.post(f"/api/runs/{run_id}/approve", json={"comment": "Smoke test."})
                    assert approved.status_code == 200 and approved.json() == {"status": "approved"}, approved.text
                    events = (await client.get(f"/api/runs/{run_id}/events")).json()["events"]
                    assert core_types(events) == expected + suffix, events
                    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
                    async with asyncio.timeout(5):
                        while len([event for event in observed if event["run_id"] == run_id]) < len(events):
                            await asyncio.sleep(0.01)
                    assert [event for event in observed if event["run_id"] == run_id] == events
                    print(f"{run_id}: " + " → ".join(core_types(events)))
                skills = (await client.get("/api/skills")).json()["skills"]
                learned = next(skill for skill in skills if skill["name"] == "distinct_count_window")
                assert learned["origin"] == "agent" and learned["status"] == "installed"
                print("HTTP, WebSocket, real sandbox, approval and reuse: OK.")
            finally:
                collector.cancel()
                await asyncio.gather(collector, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:3000")
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--idle-before-run", type=float, default=0,
                        help="Verifies an idle WebSocket for the specified duration before the first run.")
    arguments = parser.parse_args()
    if (not math.isfinite(arguments.timeout) or arguments.timeout <= 0
            or not math.isfinite(arguments.idle_before_run) or arguments.idle_before_run < 0):
        parser.error("Timeout must be positive and idle duration must be finite and nonnegative.")
    try:
        asyncio.run(smoke(arguments.base, arguments.timeout, arguments.idle_before_run))
    except (AssertionError, httpx.HTTPError, RuntimeError, TimeoutError, OSError) as exc:
        print(f"Smoke selhal: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
