# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Exactly one decision wins, even while persistence is awaiting I/O."""
from __future__ import annotations

import asyncio
import logging

from orchestrator.api_errors import ApiError


async def claim_decision(run):
    async with run.lock:
        if run.status != "awaiting_approval" or run.decision_taken or run.pending is None:
            raise ApiError(409, "NOT_AWAITING_APPROVAL", "This run is not awaiting approval.")
        run.decision_taken = True
        return run.pending


async def decision_failed(run, gatekeeper, emitter):
    await emitter.emit(run, "run_failed", "done", {"reason_code": "INTERNAL_ERROR", "reason": "Unexpected backend error."})
    try:
        await gatekeeper.discard(run.run_id)
    except Exception:
        logging.getLogger(__name__).error("Candidate cleanup after a decision error failed.")
    raise ApiError(500, "INTERNAL_ERROR", "Unexpected backend error.")


async def approve(run, comment, gatekeeper, emitter):
    pending = await claim_decision(run)
    try:
        promoted = await gatekeeper.promote(run.run_id, pending.recipe, pending.new_skills, comment)
        for skill in promoted:
            await emitter.emit(run, "skill_installed", "done", {"skill": skill})
        await emitter.emit(run, "rule_approved", "done", {"rule_name": pending.recipe["name"], "comment": comment})
    except Exception:
        logging.getLogger(__name__).error("Candidate promotion failed.")
        await decision_failed(run, gatekeeper, emitter)
    return {"status": "approved"}


async def reject(run, reason, gatekeeper, emitter):
    pending = await claim_decision(run)
    try:
        await asyncio.to_thread(gatekeeper.record_rejection, run.run_id, pending.recipe, pending.attack_type, reason)
        await gatekeeper.discard(run.run_id)
        await emitter.emit(run, "rule_rejected", "done", {"reason": reason})
    except Exception:
        logging.getLogger(__name__).error("Candidate rejection failed.")
        await decision_failed(run, gatekeeper, emitter)
    return {"status": "rejected"}
