# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from tests.unit.api_support import make_stub_app, prepare_approval


@pytest.mark.parametrize("second", ["approve", "reject"])
def test_concurrent_decisions_have_exactly_one_winner(tmp_path, second):
    app, gk = make_stub_app(tmp_path)
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json={"request": "hello"}).json()["run_id"]
        client.portal.call(prepare_approval, app, run_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = [pool.submit(client.post, f"/api/runs/{run_id}/{action}", json={"reason": "Ne."})
                         for action in ("approve", second)]
            codes = [r.result().status_code for r in responses]
        assert sorted(codes) == [200, 409]
        assert len([e for e in app.state.store.lookup(run_id).events if e["type"] in {"rule_approved", "rule_rejected"}]) == 1
        assert gk.promotions + len(gk.rejections) == 1


def test_failed_promotion_fails_run_and_discards(tmp_path):
    app, gk = make_stub_app(tmp_path)
    gk.fail_promotion = True
    with TestClient(app) as client:
        run_id = client.post("/api/runs", json={"request": "hello"}).json()["run_id"]
        client.portal.call(prepare_approval, app, run_id)
        assert client.post(f"/api/runs/{run_id}/approve", json={}).status_code == 500
        run = app.state.store.lookup(run_id)
        assert run.status == "failed"
        assert run.events[-1]["data"]["reason_code"] == "INTERNAL_ERROR"
        assert run_id in gk.discarded
