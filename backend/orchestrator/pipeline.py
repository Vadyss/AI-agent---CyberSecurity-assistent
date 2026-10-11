# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Bounded proposal → deterministic checks → independent validation → approval."""
from __future__ import annotations

import asyncio
import logging

from gatekeeper.types import SandboxError
from orchestrator.llm import LlmBudgetExceeded, LlmError, TenantBudgetExceeded, bind_run_counters, reset_run_counters
from orchestrator.run_store import PendingApproval
from orchestrator.sandbox_messages import present_sandbox_error
from orchestrator.text import clip
from orchestrator.usage import UsageLedger

logger = logging.getLogger(__name__)

REASONS = {
    "PLAN_INVALID": "The plan failed validation after 3 attempts.",
    "RULE_FAILED": "Rule did not meet the thresholds after 3 attempts.",
    "VALIDATION_FAILED": "Rule failed the validation dataset.",
    "LLM_ERROR": "The language model request failed.",
    "SANDBOX_ERROR": "Sandbox is unavailable or failed.",
    "INTERNAL_ERROR": "Unexpected backend error.",
    "QUOTA_EXCEEDED": "The tenant's quota is used up.",
}


async def run_pipeline(run, gk, roles, emitter, settings, voice=None):
    context = {"phase": "intake", "recipe": None}
    async def publish_usage(data):
        await emitter.emit(run, "llm_usage", context["phase"], data)

    usage = UsageLedger(run.run_id, settings.data_dir, publish_usage)
    token = bind_run_counters(run.stats, run_id=run.run_id, usage=usage)

    async def finish_usage():
        if usage.pending:
            await asyncio.gather(*usage.pending)
            usage.pending.clear()
        if usage.summary is None:
            await publish_usage(usage.finish())

    async def fail(reason_code, reason=None):
        if not run.events:
            await emitter.emit(run, "run_started", "intake", {"request": run.request})
        if run.status in {"running", "awaiting_approval"}:
            text = clip(reason or REASONS.get(reason_code, "The request cannot be processed."), 500)
            await finish_usage()
            await emitter.emit(run, "run_failed", context["phase"], {"reason_code": reason_code, "reason": text})
            gk.record_failure(run.run_id, reason_code, text, context["recipe"])

    async def work():
        await emitter.emit(run, "run_started", "intake", {"request": run.request})
        catalog = gk.catalog()
        samples = {source: gk.log_sample(source) for source in catalog["log_sources"]}
        lessons = gk.lessons(limit=5)
        context["phase"] = "plan"
        feedback = None
        for attempt in range(1, 4):
            raw = await roles.planner.propose(run.request, catalog, samples, lessons, feedback, attempt=attempt)
            if isinstance(raw, dict) and raw.get("attack_type") == "custom":
                verdict = await gk.prepare_custom_plan(run.run_id, raw)
            else:
                verdict = gk.check_plan(run.run_id, raw)
            if verdict.request_rejected:
                await fail("REQUEST_REJECTED", "The request cannot be processed: " + verdict.reason)
                return
            if verdict.ok:
                plan = verdict.plan
                break
            await emitter.emit(run, "policy_rejected", "plan", {"target": "plan", "name": None,
                                                               "attempt": attempt, "violations": verdict.violations})
            feedback = verdict.violations
        else:
            await fail("PLAN_INVALID")
            return
        await emitter.emit(run, "plan_ready", "plan", {"steps": plan.steps, "skills_needed": [s.name for s in plan.skills]})
        catalog = gk.catalog(run.run_id)
        for skill in plan.existing_skills:
            await emitter.emit(run, "skill_reused", "plan", {"skill": gk.skill_info(skill.name)})
            run.stats.skills_reused += 1
        gk.note_reuse(run.run_id, [s.name for s in plan.existing_skills])
        if plan.missing_skills:
            await emitter.emit(run, "capability_missing", "plan", {"skills": [{"name": s.name, "description": s.description} for s in plan.missing_skills]})
            context["phase"] = "forge"
        for spec in plan.missing_skills:
            feedback = None
            for attempt in range(1, 4):
                await emitter.emit(run, "forge_started", "forge", {"skill": spec.name, "attempt": attempt, "max_attempts": 3})
                draft = await roles.forge.build(spec, catalog, samples[plan.log_source], feedback,
                                                 attempt=attempt, request=run.request)
                result = await gk.submit_skill(run.run_id, spec, draft)
                if result.kind == "policy_rejected":
                    await emitter.emit(run, "policy_rejected", "forge", {"target": "skill", "name": spec.name, "attempt": attempt, "violations": result.violations})
                    feedback = {"violations": result.violations}
                elif result.kind == "tests_failed":
                    await emitter.emit(run, "skill_tests_failed", "forge", {"skill": spec.name, "attempt": attempt,
                        "tests_total": result.tests_total, "tests_failed": result.tests_failed,
                        "error_excerpt": present_sandbox_error(result.error_excerpt)})
                    feedback = {"failures": result.failures[:5], "error_excerpt": result.error_excerpt}
                else:
                    await emitter.emit(run, "skill_candidate_ready", "forge", {"skill": result.skill_info, "attempt": attempt,
                        "tests_total": result.tests_total, "code_sha256": result.code_sha256})
                    catalog = {**catalog, "skills": catalog["skills"] + [result.manifest]}
                    run.stats.skills_built += 1
                    break
            else:
                await fail("FORGE_FAILED", f"Skill {spec.name} failed after 3 attempts.")
                return
        context["phase"] = "rule"
        prior = gk.approved_rule(plan.attack_type)
        lessons = gk.lessons(limit=5, attack_type=plan.attack_type)
        feedback = None
        for attempt in range(1, 4):
            output = await roles.rule_author.draft(run.request, plan, catalog, samples[plan.log_source], prior, lessons, feedback, attempt=attempt)
            raw_recipe = output.get("recipe") if isinstance(output, dict) else output
            explanation = output.get("explanation", "") if isinstance(output, dict) else "The model did not return a valid proposal."
            if not isinstance(explanation, str):
                explanation = "The model did not return a valid explanation."
            context["recipe"] = raw_recipe if isinstance(raw_recipe, dict) else None
            shown = gk.display_recipe(raw_recipe)
            await emitter.emit(run, "rule_drafted", "rule", {"attempt": attempt, "max_attempts": 3, "recipe": shown, "explanation": explanation})
            verdict = gk.check_recipe(run.run_id, plan, raw_recipe)
            if not verdict.ok:
                await emitter.emit(run, "policy_rejected", "rule", {"target": "recipe", "name": shown["name"], "attempt": attempt, "violations": verdict.violations})
                feedback = {"violations": verdict.violations}
                continue
            result = await gk.evaluate_tuning(run.run_id, verdict.recipe)
            await emitter.emit(run, "rule_evaluated", "rule", {"attempt": attempt, "dataset": "tuning", "metrics": result.metrics})
            if result.metrics.passed:
                recipe, tuning = verdict.recipe, result.metrics
                context["recipe"] = recipe
                break
            feedback = {"metrics": result.metrics, "examples": result.feedback_examples}
        else:
            await fail("RULE_FAILED")
            return
        context["phase"] = "validation"
        validation = await gk.evaluate_validation(run.run_id, recipe)
        await emitter.emit(run, "validation_done", "validation", {"dataset": "validation", "metrics": validation})
        if not validation.passed:
            await fail("VALIDATION_FAILED")
            return
        context["phase"] = "approval"
        try:
            summary = await roles.summarizer.summarize(run.request, plan, recipe, tuning, validation, run.stats.snapshot())
        except Exception:
            logger.warning("The summary will use a template.")
            summary = f"Built {run.stats.skills_built} skills, reused {run.stats.skills_reused}. Rule passed tuning and validation and is awaiting approval."
        await finish_usage()
        stats = run.stats.snapshot()
        if stats["tokens_total"] is None:
            # Provider-only counters can be empty (mock, estimates); the ledger knows the full total.
            stats["tokens_total"] = usage.aggregate()["total_tokens"]
        await emitter.emit(run, "summary", "approval", {"text": summary, "stats": stats})
        if voice and voice.enabled:
            run.voice_task = asyncio.create_task(voice.speak(run, summary))
        candidates = gk.candidates_info(run.run_id)
        run.pending = PendingApproval(recipe, tuning, validation, candidates, plan.attack_type)
        await emitter.emit(run, "awaiting_approval", "approval", {"recipe": recipe, "metrics_tuning": tuning,
                                        "metrics_validation": validation, "new_skills": candidates})

    try:
        await asyncio.wait_for(work(), timeout=settings.run_timeout_s)
    except TenantBudgetExceeded:
        await fail("QUOTA_EXCEEDED", "The tenant's monthly model budget is used up. An administrator can raise it.")
    except LlmBudgetExceeded:
        await fail("LLM_ERROR", "The language model call budget was exceeded.")
    except LlmError:
        run.retryable = True  # Provider outages are usually transient.
        await fail("LLM_ERROR")
    except SandboxError:
        run.retryable = True
        await fail("SANDBOX_ERROR")
    except asyncio.TimeoutError:
        await fail("INTERNAL_ERROR", "The run exceeded its time limit.")
    except asyncio.CancelledError:
        if getattr(run, "cancel_requested", False) and run.status == "running":
            if not run.events:
                await emitter.emit(run, "run_started", "intake", {"request": run.request})
            await finish_usage()
            await emitter.emit(run, "run_cancelled", context["phase"], {"reason": "Cancelled on request."})
            record = getattr(gk, "record_cancellation", None)
            if record:
                record(run.run_id, "Cancelled on request.")
        else:
            await fail("INTERNAL_ERROR", "The run was interrupted.")
        raise
    except Exception as exc:
        logger.error("Run failed (%s).", type(exc).__name__)
        await fail("INTERNAL_ERROR")
    finally:
        logger.info("LLM run_finished run_id=%s status=%s llm_calls=%d tokens_total=%s",
                    run.run_id, run.status, run.stats.llm_calls, run.stats.tokens_total)
        try:
            if run.status == "running":
                await fail("INTERNAL_ERROR")
            if run.status in {"failed", "cancelled"}:
                await gk.discard(run.run_id)
        except Exception:
            logger.error("Failed run cleanup failed.")
        reset_run_counters(token)
