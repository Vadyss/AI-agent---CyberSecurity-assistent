# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Validate, store, then broadcast events under the run lock."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from orchestrator.models import DATA_MODELS, EventEnvelope
from orchestrator.run_store import FINISHED_STATUSES, PHASES, RunState, now_iso
from orchestrator.text import clip, safe_name
from orchestrator.ws import WebSocketHub

EVENT_TYPES = frozenset(DATA_MODELS)
TERMINAL_TYPES = {"rule_approved", "rule_rejected", "run_failed", "run_cancelled"}
STATUS_BY_EVENT = {"run_started": "running", "awaiting_approval": "awaiting_approval",
                   "rule_approved": "approved", "rule_rejected": "rejected", "run_failed": "failed",
                   "run_cancelled": "cancelled"}
EVENT_PHASE = {
    "run_started": "intake", "plan_ready": "plan", "skill_reused": "plan", "capability_missing": "plan",
    "forge_started": "forge", "skill_tests_failed": "forge", "skill_candidate_ready": "forge",
    "rule_drafted": "rule", "rule_evaluated": "rule", "validation_done": "validation",
    "summary": "approval", "voice_ready": "approval", "awaiting_approval": "approval",
    "skill_installed": "done", "rule_approved": "done", "rule_rejected": "done",
}


class InternalOrderError(RuntimeError):
    pass


def render_message(event_type: str, data: dict) -> str:
    names = lambda values: ", ".join(safe_name(v) for v in values)
    if event_type == "llm_usage":
        text = "Model usage summary is ready." if data["kind"] == "summary" else "Model call usage recorded."
    elif event_type == "run_started":
        text = "A new analyst request was received."
    elif event_type == "plan_ready":
        text = f"Plan ready: {len(data['steps'])} steps, skills {names(data['skills_needed'])}."
    elif event_type == "skill_reused":
        skill = data["skill"]
        text = f"Reusing skill {safe_name(skill['name'])} v{skill['version']}."
    elif event_type == "capability_missing":
        text = f"Missing skills: {names(s['name'] for s in data['skills'])}."
    elif event_type == "forge_started":
        text = f"The forge is building skill {safe_name(data['skill'])} (attempt {data['attempt']} of 3)."
    elif event_type == "skill_tests_failed":
        text = f"Skill {safe_name(data['skill'])} failed tests: {data['tests_failed']} of {data['tests_total']} (attempt {data['attempt']} of 3)."
    elif event_type == "skill_candidate_ready":
        text = f"Skill {safe_name(data['skill']['name'])} passed tests (attempt {data['attempt']} of 3)."
    elif event_type == "rule_drafted":
        text = f"Draft rule {safe_name(data['recipe']['name'])} (attempt {data['attempt']} of 3)."
    elif event_type in {"rule_evaluated", "validation_done"}:
        metrics = data["metrics"]
        number = lambda value: "—" if value is None else f"{value:.2f}"
        text = (f"{'Tuning' if event_type == 'rule_evaluated' else 'Validation'} dataset: "
                f"precision {number(metrics['precision'])}, recall {number(metrics['recall'])}, "
                f"{'passed' if metrics['passed'] else 'failed'}.")
    elif event_type == "summary":
        text = "The run summary is ready."
    elif event_type == "voice_ready":
        text = "The audio summary is ready."
    elif event_type == "awaiting_approval":
        text = f"Rule {safe_name(data['recipe']['name'])} is awaiting analyst approval."
    elif event_type == "skill_installed":
        text = f"Skill {safe_name(data['skill']['name'])} v{data['skill']['version']} is installed in the registry."
    elif event_type == "rule_approved":
        text = f"Rule {safe_name(data['rule_name'])} was approved."
    elif event_type == "rule_rejected":
        text = "The analyst rejected the rule."
    elif event_type == "policy_rejected":
        target = {"plan": "plan", "skill": "skill", "recipe": "recipe"}[data["target"]]
        name = f" {safe_name(data['name'])}" if data["name"] else ""
        more = f" and another {len(data['violations']) - 1}" if len(data["violations"]) > 1 else ""
        text = f"The gatekeeper rejected {target}{name}: {clip(data['violations'][0]['code'], 60)}{more}."
    elif event_type == "run_cancelled":
        text = "The run was cancelled."
    else:
        text = f"Run failed ({data['reason_code']})."
    return clip(text, 200)


class EventEmitter:
    def __init__(self, hub: WebSocketHub, repository=None, usage=None):
        self.hub = hub
        # Optional so unit tests can emit without a database.
        self.repository = repository
        self.usage = usage

    def assert_allowed(self, run: RunState, event_type: str, phase: str, data: dict) -> None:
        def fail(message):
            logging.getLogger(__name__).error("Invalid event order: %s", message)
            raise InternalOrderError(message)

        if event_type not in EVENT_TYPES or phase not in PHASES:
            fail("Unknown event type or phase.")
        if not run.events and event_type != "run_started":
            fail("A run must begin with run_started.")
        if run.events and event_type == "run_started":
            fail("The run has already started.")
        if run.status in FINISHED_STATUSES and event_type != "voice_ready":
            fail("Event after run completion.")
        if event_type in EVENT_PHASE and EVENT_PHASE[event_type] != phase:
            fail("Incorrect event phase.")
        if event_type != "voice_ready" and PHASES.index(phase) < run.phase_index:
            fail("Return to an earlier phase.")
        if event_type == "voice_ready" and not any(e["type"] == "summary" for e in run.events):
            fail("Audio before summary.")
        if event_type == "awaiting_approval":
            validations = [e for e in run.events if e["type"] == "validation_done"]
            if not validations or not validations[-1]["data"]["metrics"]["passed"]:
                fail("Approval without successful validation.")
            if not any(e["type"] == "summary" for e in run.events):
                fail("Approval before summary.")
            if run.status != "running":
                fail("Repeated awaiting_approval event.")
        if event_type in {"skill_installed", "rule_approved", "rule_rejected"}:
            if not run.decision_taken or run.status != "awaiting_approval":
                fail("Decision without analyst approval or rejection.")

    async def emit(self, run: RunState, event_type: str, phase: str, data: dict) -> dict:
        async with run.lock:
            if event_type not in EVENT_TYPES:
                raise InternalOrderError("Unknown event type.")
            clean = DATA_MODELS[event_type].model_validate(data).model_dump(mode="json")
            self.assert_allowed(run, event_type, phase, clean)
            event = EventEnvelope(type=event_type, run_id=run.run_id, seq=run.last_seq + 1,
                                  timestamp=now_iso(), phase=phase,
                                  message=render_message(event_type, clean), data=clean).model_dump(mode="json")
            status = STATUS_BY_EVENT.get(event_type, run.status)
            if self.repository is not None:
                # Durable first: an event a client has seen always exists in the log.
                finished = datetime.now(timezone.utc) if event_type in TERMINAL_TYPES else None
                await self.repository.record_event(run.run_id, event, status=status, finished_at=finished)
                if event_type == "llm_usage" and clean.get("kind") == "call" and self.usage is not None \
                        and run.tenant_id is not None:
                    await self.usage.record(run.tenant_id, run.run_id, clean["record"])
                if event_type in TERMINAL_TYPES or event_type == "summary":
                    await self.repository.save_stats(run.run_id, llm_calls=run.stats.llm_calls,
                                                     tokens_total=run.stats.tokens_total,
                                                     cost_usd=None if run.stats.cost_unknown else run.stats.cost_usd,
                                                     cost_unknown=run.stats.cost_unknown)
            run.events.append(event)
            run.last_seq += 1
            if event_type != "voice_ready":
                run.phase_index = PHASES.index(phase)
            if event_type == "run_started":
                run.stats.started_monotonic = time.monotonic()
            run.status = status
            if event_type in TERMINAL_TYPES:
                run.finished_at = event["timestamp"]
            self.hub.broadcast(event, run.tenant_id)
            return event
