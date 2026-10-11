#!/usr/bin/env python3
# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Explicit manual HTTP run against a backend; no provider calls or secrets."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlsplit

import httpx

REQUESTS = {
    "a": "I want to detect password spraying on SSH.",
    "b": "I want to detect distributed brute force on SSH.",
    "c": "I want to detect brute force on SSH.",
    "e": "I want to detect directory scanning on a web server.",
}
MAX_WAIT_S = 1500
RUN_ID = re.compile(r"run_[a-z0-9]{4,32}")
SAFE_CODE = re.compile(r"[A-Z][A-Z0-9_]{1,99}")


class EvidenceError(ValueError):
    """A safe client-side refusal; no remote body is attached."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_evidence(path: Path, evidence: dict) -> None:
    """Replace only the caller-selected evidence file, including on interruption."""
    path.parent.mkdir(parents=True, exist_ok=True)
    evidence["updated_at"] = now_iso()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(evidence, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_evidence(path: Path, base: str, scenario: str) -> dict:
    if not path.exists():
        return {"schema_version": 1, "base": base, "scenario": scenario,
                "created_at": now_iso(), "updated_at": now_iso(), "run_id": None,
                "created_response": None, "approval_response": None,
                "events_response": {"events": []}, "client_error": None, "metadata_stats": None}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("schema_version") != 1
                or value.get("base") != base or value.get("scenario") != scenario
                or "events_response" not in value):
            raise EvidenceError("EVIDENCE_MISMATCH")
        run_id = value.get("run_id")
        if run_id is not None and (not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id)):
            raise EvidenceError("EVIDENCE_MISMATCH")
        return value
    except (OSError, ValueError, TypeError):
        raise EvidenceError("EVIDENCE_MISMATCH") from None


def validate_events(payload: object, run_id: str) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise EvidenceError("INVALID_EVENTS_RESPONSE")
    events = payload["events"]
    if any(not isinstance(event, dict) or event.get("run_id") != run_id
           or type(event.get("seq")) is not int or event["seq"] != index
           or not isinstance(event.get("type"), str) or not isinstance(event.get("data"), dict)
           for index, event in enumerate(events, 1)):
        raise EvidenceError("INVALID_EVENT_SEQUENCE")
    return events


def run_status(events: list[dict]) -> str:
    for event in reversed(events):
        if event["type"] in {"rule_approved", "rule_rejected", "run_failed", "awaiting_approval"}:
            return {"rule_approved": "approved", "rule_rejected": "rejected",
                    "run_failed": "failed", "awaiting_approval": "awaiting_approval"}[event["type"]]
    return "running"


def skill_metadata(skill: object) -> dict:
    if not isinstance(skill, dict):
        return {}
    return {key: skill[key] for key in ("name", "version", "kind", "origin", "status") if key in skill}


def summarize(evidence: dict, elapsed_s: float) -> dict:
    # The full unmodified events response is persisted separately. These fields
    # make metrics and injection behavior easy to compare without provider text.
    response = evidence.get("events_response")
    events = response.get("events", []) if isinstance(response, dict) else []
    events = events if isinstance(events, list) else []
    events = [event for event in events if isinstance(event, dict) and isinstance(event.get("data"), dict)
              and isinstance(event.get("type"), str) and type(event.get("seq")) is int]
    validations = [event["data"].get("metrics") for event in events if event.get("type") == "validation_done"]
    summaries = [event["data"].get("stats") for event in events if event.get("type") == "summary"]
    metadata_stats = evidence.get("metadata_stats")
    metadata_stats = {key: metadata_stats[key] for key in ("duration_ms", "llm_calls", "tokens_total",
                      "skills_built", "skills_reused") if key in metadata_stats} if isinstance(metadata_stats, dict) else None
    approvals = [event["data"] for event in events if event.get("type") == "awaiting_approval"]
    status = run_status(events) if evidence.get("run_id") else "not_started"
    rejections = [{"seq": event["seq"], "target": event["data"].get("target"),
                   "attempt": event["data"].get("attempt"),
                   "codes": [violation.get("code") for violation in event["data"].get("violations", [])
                             if isinstance(violation, dict)]}
                  for event in events if event.get("type") == "policy_rejected"]
    final_recipe = approvals[-1].get("recipe", {}) if approvals else {}
    final_recipe = final_recipe if isinstance(final_recipe, dict) else {}
    drafts = [{"attempt": event["data"].get("attempt"),
               "filter": event["data"]["recipe"].get("filter", [])}
              for event in events if event.get("type") == "rule_drafted"
              and isinstance(event["data"].get("recipe"), dict)]
    failure = next((event["data"].get("reason_code") for event in reversed(events)
                    if event.get("type") == "run_failed"), None)
    return {
        "scenario": evidence["scenario"], "run_id": evidence.get("run_id"),
        "status": status, "success": status == "approved" and evidence.get("client_error") is None,
        "reason_code": failure, "client_error": evidence.get("client_error"),
        "wall_duration_ms": round(elapsed_s * 1000),
        "event_types": [event["type"] for event in events],
        "tuning_attempts": [{"attempt": event["data"].get("attempt"),
                            "dataset": event["data"].get("dataset"),
                            "metrics": event["data"].get("metrics")}
                           for event in events if event.get("type") == "rule_evaluated"],
        "validation_metrics": validations[-1] if validations else None,
        "validation_count": len(validations), "stats": summaries[-1] if summaries else metadata_stats,
        "stats_source": "summary_event" if summaries else "metadata_logs" if metadata_stats is not None else None,
        "candidate_skills": [skill_metadata(event["data"].get("skill"))
                             for event in events if event.get("type") == "skill_candidate_ready"],
        "reused_skills": [skill_metadata(event["data"].get("skill"))
                          for event in events if event.get("type") == "skill_reused"],
        "installed_skills": [skill_metadata(event["data"].get("skill"))
                             for event in events if event.get("type") == "skill_installed"],
        "policy_rejections": rejections,
        "rule_filters": drafts,
        "approved_filter": final_recipe.get("filter", []) if status == "approved" else None,
        "recipe_exception_rejected": any("RECIPE_EXCEPTION" in item["codes"] for item in rejections),
    }


def assert_approval_ready(events: list[dict]) -> None:
    validations = [event["data"].get("metrics") for event in events if event["type"] == "validation_done"]
    pending = [event["data"] for event in events if event["type"] == "awaiting_approval"]
    if (len(validations) != 1 or not isinstance(validations[0], dict)
            or validations[0].get("passed") is not True or not pending
            or pending[-1].get("metrics_validation") != validations[0]
            or not isinstance(pending[-1].get("metrics_tuning"), dict)
            or pending[-1]["metrics_tuning"].get("passed") is not True):
        raise EvidenceError("APPROVAL_WITHOUT_PASSED_VALIDATION")


async def run_live(base: str, scenario: str, output: Path, *, transport=None,
                   timeout_s: float = MAX_WAIT_S, poll_s: float = 2) -> dict:
    evidence = load_evidence(output, base, scenario)
    evidence["client_error"] = None
    started = time.monotonic()

    def save() -> None:
        evidence["summary"] = summarize(evidence, time.monotonic() - started)
        write_evidence(output, evidence)

    save()
    try:
        async with httpx.AsyncClient(base_url=base, timeout=30, transport=transport,
                                     trust_env=False, follow_redirects=False) as client:
            async with asyncio.timeout(timeout_s):
                if evidence["run_id"] is None:
                    created = await client.post("/api/runs", json={"request": REQUESTS[scenario]})
                    created.raise_for_status()
                    if created.status_code != 202:
                        raise EvidenceError("INVALID_CREATE_STATUS")
                    payload = created.json()
                    if (not isinstance(payload, dict) or payload.get("status") != "running"
                            or not isinstance(payload.get("run_id"), str)
                            or not RUN_ID.fullmatch(payload["run_id"])):
                        raise EvidenceError("INVALID_CREATE_RESPONSE")
                    evidence["created_response"] = payload
                    evidence["run_id"] = payload["run_id"]
                    save()
                run_id = evidence["run_id"]
                approval_submitted = False
                while True:
                    answer = await client.get(f"/api/runs/{run_id}/events")
                    answer.raise_for_status()
                    evidence["events_response"] = answer.json()
                    events = validate_events(evidence["events_response"], run_id)
                    save()
                    status = run_status(events)
                    if status in {"failed", "rejected", "approved"}:
                        if status == "approved":
                            assert_approval_ready(events)
                        break
                    if status == "awaiting_approval" and not approval_submitted:
                        assert_approval_ready(events)
                        approved = await client.post(f"/api/runs/{run_id}/approve",
                            json={"comment": "Manual verification of the live backend."})
                        approved.raise_for_status()
                        evidence["approval_response"] = approved.json()
                        if evidence["approval_response"] != {"status": "approved"}:
                            raise EvidenceError("INVALID_APPROVAL_RESPONSE")
                        approval_submitted = True
                        save()
                        continue
                    await asyncio.sleep(poll_s)
    except httpx.HTTPStatusError as exc:
        evidence["client_error"] = {"kind": "HTTP_STATUS", "status": exc.response.status_code}
        try:
            code = exc.response.json().get("error", {}).get("code")
            if isinstance(code, str) and SAFE_CODE.fullmatch(code):
                evidence["client_error"]["code"] = code
        except (ValueError, AttributeError, TypeError):
            pass
    except (TimeoutError, httpx.TimeoutException):
        evidence["client_error"] = {"kind": "TIMEOUT"}
    except EvidenceError as exc:
        evidence["client_error"] = {"kind": str(exc)}
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        evidence["client_error"] = {"kind": "TRANSPORT_OR_PROTOCOL"}
    except asyncio.CancelledError:
        evidence["client_error"] = {"kind": "INTERRUPTED"}
        raise
    finally:
        save()
    return evidence["summary"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://localhost:3000", help="Backend origin without credentials or query.")
    parser.add_argument("--scenario", choices=tuple(REQUESTS), required=True)
    parser.add_argument("--output", type=Path, required=True, help="Evidence JSON file; matching existing run is resumed.")
    arguments = parser.parse_args()
    base = arguments.base.rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        parser.error("--base must be an HTTP(S) URL without credentials or a query string.")
    try:
        summary = asyncio.run(run_live(base, arguments.scenario, arguments.output))
    except EvidenceError:
        parser.error("--output contains mismatched or invalid evidence; use the matching file.")
    except KeyboardInterrupt:
        print('{"client_error":{"kind":"INTERRUPTED"}}', file=sys.stderr)
        raise SystemExit(130) from None
    except OSError:
        print('{"client_error":{"kind":"EVIDENCE_IO"}}', file=sys.stderr)
        raise SystemExit(1) from None
    # Never print event bodies, explanations, source, prompts, or rule literals.
    public = {key: summary[key] for key in ("scenario", "run_id", "status", "success", "reason_code",
              "client_error", "wall_duration_ms", "event_types", "validation_metrics", "stats", "stats_source")}
    print(json.dumps(public, ensure_ascii=False, allow_nan=False))
    raise SystemExit(0 if summary["success"] else 1)


if __name__ == "__main__":
    main()
