# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace


from gatekeeper.types import Metrics
from tests.fakes import make_settings
from orchestrator.main import create_app
from orchestrator.run_store import PendingApproval

METRICS = Metrics(true_positives=8, false_positives=0, false_negatives=0,
                  precision=1, recall=1, passed=True,
                  thresholds={"min_precision": .9, "min_recall": .9})


class StubSandbox:
    async def health(self):
        return True

    async def close(self):
        pass


class StubGatekeeper:
    def __init__(self):
        self.promotions = 0
        self.rejections = []
        self.discarded = []
        self.fail_promotion = False

    def installed_skills(self):
        return []

    async def promote(self, *args):
        await asyncio.sleep(.01)
        if self.fail_promotion:
            raise OSError("storage failed")
        self.promotions += 1
        return []

    def record_rejection(self, *args):
        self.rejections.append(args)

    async def discard(self, run_id):
        self.discarded.append(run_id)


async def held_pipeline(run, gk, roles, emitter, settings, voice):
    await emitter.emit(run, "run_started", "intake", {"request": run.request})
    await asyncio.Event().wait()


def make_stub_app(tmp_path):
    gk = StubGatekeeper()
    return create_app(settings=replace(make_settings(), data_dir=tmp_path, llm_provider="mock", mock_delay_ms=0),
                      gatekeeper=gk, sandbox=StubSandbox(), roles=SimpleNamespace(),
                      pipeline_runner=held_pipeline), gk


async def prepare_approval(app, run_id):
    run = app.state.store.lookup(run_id)
    if not run.events:
        await asyncio.sleep(.01)
    await app.state.emitter.emit(run, "validation_done", "validation", {"dataset": "validation", "metrics": METRICS})
    await app.state.emitter.emit(run, "summary", "approval", {"text": "Done.", "stats": run.stats.snapshot()})
    recipe = {"name": "test_rule"}
    run.pending = PendingApproval(recipe, METRICS, METRICS, [], "ssh_bruteforce")
    await app.state.emitter.emit(run, "awaiting_approval", "approval", {
        "recipe": recipe, "metrics_tuning": METRICS, "metrics_validation": METRICS, "new_skills": []})
