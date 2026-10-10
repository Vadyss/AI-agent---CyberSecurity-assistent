# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""Public boundary: proposals in, deterministic verdicts out."""
from __future__ import annotations

import asyncio
from copy import deepcopy

from .audit import AUDIT_ACTOR, AuditLog
from .audited_sandbox import AuditedSandbox, audit_run
from .datasets import DatasetStore
from .evaluator import evaluate_recipe
from .examiner_data import prepare_datasets
from .hidden_tests import run_hidden_tests
from .lessons import Lessons
from .manifest import validate_manifest
from .names import require_run_id, violations_unique, valid_name, clip
from .plan_check import check_plan
from .policy import load_policy
from .recipe import check_recipe, display_recipe
from .registry import IntegrityError, Registry, UsageStore, recipe_sha256
from .sandbox_client import SandboxClient
from .static_analysis import analyze_code
from .types import GatekeeperConfig, ParseFailure, PlanVerdict, SkillVerdict, Violation


# Re-exported for the orchestrator, which may only import this public boundary.
__all__ = ["AUDIT_ACTOR", "Gatekeeper", "IntegrityError", "SandboxClient", "UsageStore", "load_policy"]


class Gatekeeper:
    def __init__(self, cfg: GatekeeperConfig, sandbox, examiner=None, observer=None):
        self.policy = load_policy(cfg.policy_path)
        self.datasets = DatasetStore(cfg.datasets_dir, self.policy.datasets.min_instances_per_attack_type,
                                     self.policy.datasets.sample_lines)
        self.sandbox = sandbox
        self.audit = AuditLog(cfg.data_dir)
        self.registry = Registry(cfg.data_dir, self.audit)
        self.registry.initialize(cfg.seed_skills_dir)
        # ``sandbox`` stays the raw client; every execution goes through ``executor``.
        self.executor = AuditedSandbox(sandbox, self.audit, self.registry.describe_digest, observer)
        self._lessons = Lessons(cfg.data_dir)
        self._plans = {}
        self._checked = {}
        self._tuning = {}
        self._validated = {}
        self._validation_started = set()
        self._last_recipes = {}
        self._skill_attempts = {}
        self._custom_data = {}
        self._custom_attacks = {}
        self._examiner_started = set()
        self.examiner = examiner
        self.examiner_enabled = cfg.examiner_enabled
        self.audit.append("startup", detail={"policy_sha256": self.policy.sha256,
                                             "datasets_manifest_sha256": self.datasets.manifest_sha256,
                                             "datasets": self.datasets.digests})

    @property
    def max_llm_calls(self):
        return self.policy.llm.max_calls_per_run

    def catalog(self, run_id=None):
        data = self.datasets.catalog()
        if run_id in self._custom_attacks:
            custom = self._custom_attacks[run_id]
            data["attack_types"][custom["slug"]] = {"log_source": "ssh", "description": custom["description"]}
        return {**data, "skills": self.registry.manifests(run_id),
                "policy": self.policy.digest_for_llm(),
                "examiner_enabled": self.examiner_enabled and self.examiner is not None}

    def log_sample(self, source):
        return self.datasets.sample(source)

    def lessons(self, limit=5, attack_type=None):
        return self._lessons.read(limit, attack_type)

    def approved_rule(self, attack_type):
        return self.registry.approved_rule(attack_type)

    def skill_info(self, name):
        return self.registry.skill_info(name)

    def installed_skills(self):
        return self.registry.installed_skills()

    def candidates_info(self, run_id):
        return self.registry.candidate_infos(run_id)

    def review_candidate(self, run_id, name):
        """What a human reviews before approving: the candidate's verified code and tests."""
        require_run_id(run_id)
        if not valid_name(name) or name not in {info.name for info in self.registry.candidate_infos(run_id)}:
            raise KeyError(name)
        return self.registry.review(run_id, name)

    def review_skill(self, name):
        if not valid_name(name):
            raise KeyError(name)
        return self.registry.review(None, name)

    def check_plan(self, run_id, raw):
        require_run_id(run_id)
        if isinstance(raw, dict) and raw.get("attack_type") == "custom":
            return PlanVerdict(request_rejected=True, reason="Independent test data must be created for this attack type first.")
        verdict = check_plan(raw, self.catalog(run_id), self.policy)
        if verdict.ok:
            self._plans[run_id] = deepcopy(verdict.plan)
        elif verdict.request_rejected:
            self.audit.append("request_rejected", run_id, {"reason": verdict.reason})
        else:
            self.audit.append("plan_rejected", run_id, {"violations": [v.model_dump() for v in verdict.violations]})
        return verdict

    async def prepare_custom_plan(self, run_id, raw):
        require_run_id(run_id)
        raw = deepcopy(raw)
        reason = "Verifiable test data could not be prepared for this attack type."
        try:
            custom = raw.get("custom_attack") if isinstance(raw, dict) else None
            if (not self.examiner_enabled or self.examiner is None or run_id in self._examiner_started
                    or raw.get("attack_type") != "custom" or raw.get("intent") != "detection_rule" or raw.get("log_source") != "ssh"
                    or not isinstance(custom, dict) or set(custom) != {"slug", "description"}
                    or not valid_name(custom.get("slug")) or custom["slug"] in self.datasets.catalog()["attack_types"]
                    or not isinstance(custom.get("description"), str) or not custom["description"].strip()
                    or len(custom["description"]) > 300):
                raise ValueError(reason)
            custom["description"].encode("utf-8")
            description = clip(custom["description"], 300).strip()
            if not description:
                raise ValueError(reason)
            # Reject an invalid plan before spending the independent examination.
            # A corrected planner attempt can then use the normal three attempts.
            normalized = deepcopy(raw)
            normalized["attack_type"] = custom["slug"]
            normalized["custom_attack"]["description"] = description
            catalog = self.catalog(run_id)
            catalog["attack_types"][custom["slug"]] = {"log_source": "ssh", "description": description}
            preflight = check_plan(normalized, catalog, self.policy)
            if not preflight.ok:
                self.audit.append("plan_rejected", run_id, {"violations": [v.model_dump() for v in preflight.violations]})
                return preflight
            self._examiner_started.add(run_id)
            fmt = self.datasets.catalog()["log_sources"]["ssh"]["description"]
            generated = await self.examiner(description, fmt)
            if not isinstance(generated, dict) or set(generated) != {"code"} or not isinstance(generated["code"], str):
                raise ValueError(reason)
            baseline = {name: self.datasets.load("ssh", name) for name in ("tuning", "validation")}
            with audit_run(run_id):
                prepared = await prepare_datasets(run_id, custom["slug"], generated["code"], baseline,
                                                 self.executor, self.policy, lambda name: self.registry.read_skill(None, name))
            digests = self.registry.save_examiner_data(run_id, prepared)
            self._custom_data[run_id] = prepared
            self._custom_attacks[run_id] = {"slug": custom["slug"], "description": description}
            self.audit.append("examiner_dataset", run_id, {"attack_type": custom["slug"], "digests": digests,
                              "counts": {name: len(dataset.labels["instances"]) for name, dataset in prepared.items()}})
            return self.check_plan(run_id, normalized)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.audit.append("request_rejected", run_id, {"reason": reason})
            return PlanVerdict(request_rejected=True, reason=reason)

    async def submit_skill(self, run_id, spec, draft):
        spec, draft = deepcopy(spec), deepcopy(draft)
        require_run_id(run_id)
        plan = self._plans.get(run_id)
        if plan is None or not any(s.model_dump() == spec.model_dump() for s in plan.missing_skills):
            raise IntegrityError("The skill does not match the approved plan.")
        key = (run_id, spec.name)
        self._skill_attempts[key] = self._skill_attempts.get(key, 0) + 1
        if self._skill_attempts[key] > self.policy.attempts.forge_per_skill:
            raise IntegrityError("The forge attempt limit has been exceeded.")
        violations = []
        if spec.name == "data":
            return SkillVerdict(kind="policy_rejected", violations=[Violation(code="INVALID_NAME", detail="The name data is reserved for internal test data.")])
        if (not isinstance(draft, dict) or not isinstance(draft.get("code"), str)
                or not isinstance(draft.get("tests"), str) or not isinstance(draft.get("manifest"), dict)):
            violations = [Violation(code="INVALID_OUTPUT", detail="The forge must return a manifest, code and tests.")]
            manifest = None
        else:
            manifest, violations = validate_manifest(draft["manifest"], spec, self.policy)
            # Analyze even invalid manifests to report all safe-to-compute violations.
            declared = manifest or {"imports": []}
            violations += analyze_code(draft["code"], declared, self.policy)
            violations += analyze_code(draft["tests"], declared, self.policy, is_test=True)
        violations = violations_unique(violations)
        if violations:
            self.audit.append("skill_rejected", run_id, {"skill": spec.name,
                              "attempt": self._skill_attempts[key], "violations": [v.model_dump() for v in violations]})
            return SkillVerdict(kind="policy_rejected", violations=violations)
        with audit_run(run_id):
            result = await self.executor.test(draft["code"], draft["tests"],
                                              allowed_imports=list(self.policy.skills.allowed_imports),
                                              timeout_s=self.policy.sandbox.test_timeout_s)
        report = result["tests"]
        total = report["total"]
        failed = report["failed"]
        failures = report["failures"].copy()
        if result["status"] != "ok" and failed == 0:
            failed += 1
            total += 1
            failures.append({"name": "execution", "error": "Time limit exceeded." if result["status"] == "timeout" else "Skill tests failed."})
        if report["total"] < self.policy.skills.min_llm_tests:
            total += 1
            failed += 1
            failures.append({"name": "test_count", "error": "The skill contains too few tests."})
        with audit_run(run_id):
            hidden_total, hidden_failures = await run_hidden_tests(manifest, draft["code"], self.executor, self.policy,
                 parser_lines=self.datasets.parser_lines(plan.log_source) if spec.role == "parser" else None)
        total += hidden_total
        failed += len(hidden_failures)
        failures.extend(hidden_failures)
        if failed:
            self.audit.append("skill_tests_failed", run_id, {"skill": spec.name, "attempt": self._skill_attempts[key],
                                                            "tests_total": total, "tests_failed": failed})
            return SkillVerdict(kind="tests_failed", tests_total=total, tests_failed=failed,
                                failures=failures[:5], error_excerpt=failures[0]["error"][:500] if failures else "Tests failed.")
        info = self.registry.save_candidate(run_id, manifest, draft["code"], draft["tests"], self._skill_attempts[key])
        meta = self.registry.read_skill(run_id, spec.name)[2]
        return SkillVerdict(kind="candidate", skill_info=info, manifest=manifest, tests_total=total,
                            code_sha256=meta["sha256"])

    def display_recipe(self, raw):
        return display_recipe(raw)

    def check_recipe(self, run_id, plan, raw):
        require_run_id(run_id)
        stored = self._plans.get(run_id)
        if stored is None or stored.model_dump() != plan.model_dump():
            raise IntegrityError("The recipe does not match the approved plan.")
        manifests = {m["name"]: m for m in self.registry.manifests(run_id)}
        verdict = check_recipe(raw, stored, manifests, self.policy)
        if verdict.ok:
            self._checked[run_id] = recipe_sha256(verdict.recipe)
            self._last_recipes[run_id] = deepcopy(verdict.recipe)
        else:
            self.audit.append("recipe_rejected", run_id, {"violations": [v.model_dump() for v in verdict.violations]})
        return verdict

    def _skill_digests(self, run_id, recipe):
        names = [recipe["parser"], recipe["aggregation"]["skill"]] + [s["skill"] for s in recipe.get("enrich", [])]
        return {name: self.registry.read_skill(run_id, name)[2]["sha256"] for name in names}

    async def evaluate_tuning(self, run_id, recipe):
        recipe = deepcopy(recipe)
        require_run_id(run_id)
        if self._checked.get(run_id) != recipe_sha256(recipe):
            raise IntegrityError("The recipe did not pass gatekeeper checks.")
        plan = self._plans[run_id]
        data = self._dataset(run_id, plan.log_source, "tuning")
        digests = self._skill_digests(run_id, recipe)
        with audit_run(run_id):
            result = await evaluate_recipe(recipe, data.lines, data.labels, lambda name: self.registry.read_skill(run_id, name),
                                           self.executor, self.policy, feedback=True, audit=self.audit, run_id=run_id)
        if digests != self._skill_digests(run_id, recipe):
            raise IntegrityError("The skill changed during evaluation.")
        self._tuning[run_id] = {"recipe_sha256": recipe_sha256(recipe), "metrics": result.metrics,
                               "skill_digests": digests}
        self.audit.append("rule_evaluated", run_id, {"dataset": "tuning", "metrics": result.metrics.model_dump(mode="json"),
                                                    "cross_type_hits": result.cross_type_hits})
        return result

    async def evaluate_validation(self, run_id, recipe):
        recipe = deepcopy(recipe)
        require_run_id(run_id)
        tuning = self._tuning.get(run_id)
        if (not tuning or not tuning["metrics"].passed or tuning["recipe_sha256"] != recipe_sha256(recipe)
                or run_id in self._validation_started):
            raise IntegrityError("Validation requires successful tuning and can only be performed once.")
        digests = self._skill_digests(run_id, recipe)
        if digests != tuning["skill_digests"]:
            raise IntegrityError("The skill changed after tuning.")
        self._validation_started.add(run_id)
        plan = self._plans[run_id]
        data = self._dataset(run_id, plan.log_source, "validation")
        with audit_run(run_id):
            result = await evaluate_recipe(recipe, data.lines, data.labels, lambda name: self.registry.read_skill(run_id, name),
                                           self.executor, self.policy, audit=self.audit, run_id=run_id)
        if digests != self._skill_digests(run_id, recipe):
            raise IntegrityError("The skill changed during validation.")
        self._validated[run_id] = {"recipe_sha256": recipe_sha256(recipe), "attack_type": plan.attack_type,
                                 "metrics_tuning": tuning["metrics"], "metrics_validation": result.metrics,
                                 "skill_digests": digests}
        self.audit.append("validation_done", run_id, {"metrics": result.metrics.model_dump(mode="json"),
                                                     "cross_type_hits": result.cross_type_hits})
        return result.metrics

    def note_reuse(self, run_id, names):
        self.registry.note_reuse(run_id, names)

    async def promote(self, run_id, recipe, new_skills, comment):
        recipe, new_skills = deepcopy(recipe), deepcopy(new_skills)
        result = await asyncio.to_thread(self.registry.promote, run_id, recipe, new_skills,
                                         deepcopy(self._validated.get(run_id, {})), comment)
        self._forget(run_id)
        return result

    def record_rejection(self, run_id, recipe, attack_type, reason):
        require_run_id(run_id)
        self.audit.append("rule_rejected", run_id, {"reason": reason, "attack_type": attack_type})
        self._lessons.record(run_id, attack_type, "analyst_rejected", reason, recipe)

    def record_cancellation(self, run_id, reason):
        require_run_id(run_id)
        self.audit.append("run_cancelled", run_id, {"reason": reason})

    def record_failure(self, run_id, reason_code, reason, recipe=None):
        self.audit.append("run_failed", run_id, {"reason_code": reason_code, "reason": reason})
        if reason_code in {"RULE_FAILED", "VALIDATION_FAILED"}:
            plan = self._plans.get(run_id)
            tuning = self._tuning.get(run_id, {}).get("metrics")
            validation = self._validated.get(run_id, {}).get("metrics_validation")
            metric_text = ""
            for label, metrics in (("Tuning", tuning), ("Validation", validation)):
                if metrics:
                    metric_text += f" {label}: precision={metrics.precision}, recall={metrics.recall}."
            self._lessons.record(run_id, plan.attack_type if plan else "unknown", reason_code,
                                 reason + metric_text, self._last_recipes.get(run_id))

    def _dataset(self, run_id, source, dataset):
        if run_id in self._custom_data:
            return deepcopy(self._custom_data[run_id][dataset])
        return self.datasets.load(source, dataset)

    def _forget(self, run_id):
        for state in (self._plans, self._checked, self._tuning, self._validated, self._last_recipes):
            state.pop(run_id, None)
        self._validation_started.discard(run_id)
        self._examiner_started.discard(run_id)
        self._custom_data.pop(run_id, None)
        self._custom_attacks.pop(run_id, None)
        self._skill_attempts = {k: v for k, v in self._skill_attempts.items() if k[0] != run_id}

    async def discard(self, run_id):
        await asyncio.to_thread(self.registry.discard, run_id)
        self._forget(run_id)
