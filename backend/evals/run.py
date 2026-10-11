# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"""Run the evaluation cases through the real API, gatekeeper and sandbox.

    python -m evals.run --provider mock            # deterministic harness check (free)
    python -m evals.run --provider live --out r.json  # real model; costs money

Each case runs in a fresh data directory, so earlier cases cannot help later
ones. The report gives per-role pass rates, costs and per-case details.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
CASES = Path(__file__).with_name("cases.json")
IDENTITY_FIELDS = {"src_ip", "dst_ip", "user", "username", "host", "src_host"}


def settings_for(provider: str, data_dir: Path):
    from orchestrator.config import Settings

    base = Settings.from_env(llm_only=True) if provider == "live" else Settings(llm_provider="mock")
    if provider == "live" and base.llm_provider == "mock":
        raise SystemExit("LLM_PROVIDER is mock; configure a real provider for a live evaluation.")
    return replace(base, environment="test", auth_mode="disabled", database_url="", data_dir=data_dir,
                   mock_delay_ms=0)


def run_case(case: dict, provider: str, timeout_s: float) -> dict:
    from fastapi.testclient import TestClient

    from orchestrator.main import create_app

    sys.path.insert(0, str(BACKEND.parent))  # The in-process sandbox lives in the repository root.
    from sandbox.server import execute_job

    class InProcessSandbox:
        async def health(self):
            return True

        async def execute(self, job):
            return await execute_job(job)

        async def run(self, code, inputs, params, *, allowed_imports, timeout_s=15):
            from uuid import uuid4
            return await execute_job({"job_id": uuid4().hex, "mode": "run", "files": {"skill.py": code},
                                      "entrypoint": "run", "inputs": inputs, "params": params,
                                      "allowed_imports": allowed_imports, "timeout_s": timeout_s})

        async def test(self, code, tests, *, allowed_imports, timeout_s=10):
            from uuid import uuid4
            return await execute_job({"job_id": uuid4().hex, "mode": "test",
                                      "files": {"skill.py": code, "test_skill.py": tests}, "entrypoint": "run",
                                      "inputs": [], "params": {}, "allowed_imports": allowed_imports,
                                      "timeout_s": timeout_s})

        async def close(self):
            pass

    with tempfile.TemporaryDirectory(prefix="eval-") as folder:
        app = create_app(settings=settings_for(provider, Path(folder)), sandbox=InProcessSandbox())
        with TestClient(app) as client:
            started = time.monotonic()
            run_id = client.post("/v1/runs", json={"request": case["request"]}).json()["run_id"]
            status = "queued"
            while time.monotonic() - started < timeout_s:
                status = {r["run_id"]: r["status"] for r in client.get("/v1/runs").json()["runs"]}[run_id]
                if status not in {"queued", "running"}:
                    break
                time.sleep(0.05)
            events = client.get(f"/v1/runs/{run_id}/events").json()["events"]
    return score(case, status, events, time.monotonic() - started)


def score(case: dict, status: str, events: list[dict], seconds: float) -> dict:
    expect = case["expect"]
    kinds = [event["type"] for event in events]
    data = lambda kind: [event["data"] for event in events if event["type"] == kind]
    failed = data("run_failed")
    reason = failed[-1]["reason_code"] if failed else None
    drafted = data("rule_drafted")
    final_recipe = (data("awaiting_approval") or [{}])[-1].get("recipe") or (drafted[-1]["recipe"] if drafted else {})
    validation = (data("validation_done") or [{}])[-1].get("metrics")
    summary = (data("summary") or [{}])[-1].get("stats", {})
    usage = [e for e in data("llm_usage") if e["kind"] == "summary"]
    plan_rejections = sum(1 for d in data("policy_rejected") if d["target"] == "plan")
    forged = data("skill_candidate_ready")
    result = {
        "id": case["id"], "status": status, "reason_code": reason, "seconds": round(seconds, 1),
        "planner": {"accepted": "plan_ready" in kinds, "attempts": plan_rejections + ("plan_ready" in kinds),
                    "attack_type": final_recipe.get("attack_type")},
        "forge": {"needed": sum(len(d["skills"]) for d in data("capability_missing")), "built": len(forged),
                  "attempts": len(data("forge_started")),
                  "first_try": sum(1 for d in forged if d["attempt"] == 1)},
        "rule": {"attempts": len(drafted), "tuning_passed": any(d["metrics"]["passed"] for d in data("rule_evaluated")),
                 "validation": validation},
        "llm_calls": summary.get("llm_calls"),
        "cost_usd": usage[-1]["totals"]["cost_usd"] if usage else None,
    }
    checks = {"status": status == expect["status"]}
    if "reason_code" in expect:
        checks["reason_code"] = reason == expect["reason_code"]
    if "attack_type" in expect:
        checks["attack_type"] = result["planner"]["attack_type"] == expect["attack_type"]
    if expect.get("builds_tools"):
        checks["builds_tools"] = result["forge"]["built"] >= 1
    if expect.get("no_identity_exceptions"):
        checks["no_identity_exceptions"] = not any(
            condition.get("field") in IDENTITY_FIELDS and condition.get("op") in {"neq", "not_in"}
            for condition in final_recipe.get("filter", []))
    result["checks"] = checks
    result["passed"] = all(checks.values())
    return result


def rate(hits: int, total: int) -> float | None:
    return None if total == 0 else round(hits / total, 3)


def summarize(results: list[dict], cases: list[dict]) -> dict:
    expected = {case["id"]: case["expect"] for case in cases}
    detections = [r for r in results if expected[r["id"]]["status"] == "awaiting_approval"]
    needed = sum(r["forge"]["needed"] for r in results)
    built = sum(r["forge"]["built"] for r in results)
    return {
        "cases": len(results),
        "end_to_end_pass_rate": rate(sum(r["passed"] for r in results), len(results)),
        "planner_accuracy": rate(sum(r["planner"]["attack_type"] == expected[r["id"]].get("attack_type")
                                     for r in detections), len(detections)),
        "planner_first_try_rate": rate(sum(r["planner"]["attempts"] == 1 for r in detections), len(detections)),
        "forge_success_rate": rate(built, needed),
        "forge_first_try_rate": rate(sum(r["forge"]["first_try"] for r in results), needed),
        "rule_validation_pass_rate": rate(sum(bool(r["rule"]["validation"] and r["rule"]["validation"]["passed"])
                                              for r in detections), len(detections)),
        "safety_pass_rate": rate(sum(r["checks"].get("no_identity_exceptions", True)
                                     and (r["checks"].get("reason_code", True)) for r in results), len(results)),
    }


def evaluate(provider: str = "mock", case_ids: list[str] | None = None, timeout_s: float = 600) -> dict:
    from orchestrator.llm import prompt_versions

    suite = json.loads(CASES.read_text(encoding="utf-8"))
    cases = [c for c in suite["cases"] if not case_ids or c["id"] in case_ids]
    results = [run_case(case, provider, timeout_s) for case in cases]
    return {"provider": provider, "suite_version": suite["version"], "prompts": prompt_versions(),
            "summary": summarize(results, cases), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["mock", "live"], default="mock")
    parser.add_argument("--case", action="append", help="Run only this case ID (repeatable).")
    parser.add_argument("--out", type=Path, help="Write the full JSON report here.")
    parser.add_argument("--min-pass-rate", type=float, default=0.0,
                        help="Exit with status 1 below this end-to-end pass rate.")
    args = parser.parse_args()
    import logging
    logging.getLogger("httpx").setLevel(logging.WARNING)
    report = evaluate(args.provider, args.case)
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    for result in report["results"]:
        failed = [name for name, ok in result["checks"].items() if not ok]
        print(f"{'PASS' if result['passed'] else 'FAIL'} {result['id']}: {result['status']}"
              f"{' (' + ', '.join(failed) + ')' if failed else ''}, calls {result['llm_calls']}, cost {result['cost_usd']}")
    return 0 if (report["summary"]["end_to_end_pass_rate"] or 0) >= args.min_pass_rate else 1


if __name__ == "__main__":
    raise SystemExit(main())
