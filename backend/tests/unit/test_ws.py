# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from orchestrator.ws import WebSocketHub
from tests.unit.api_support import make_stub_app


def test_websocket_replay_consistency_and_no_history(tmp_path):
    app, gk = make_stub_app(tmp_path)
    with TestClient(app) as client:
        first = client.post("/api/runs", json={"request": "old"}).json()["run_id"]
        async def finish():
            run = app.state.store.lookup(first)
            await app.state.emitter.emit(run, "run_failed", "plan", {"reason_code": "REQUEST_REJECTED", "reason": "Ne."})
        client.portal.call(finish)
        with client.websocket_connect("/api/ws") as ws:
            current = client.post("/api/runs", json={"request": "new"}).json()["run_id"]
            event = ws.receive_json()
            assert event["run_id"] == current
            assert event == client.get(f"/api/runs/{current}/events").json()["events"][0]


async def test_slow_subscriber_does_not_block_fast_client():
    class Socket:
        def __init__(self, slow=False):
            self.slow = slow
            self.events = []
            self.closed = False
        async def accept(self):
            pass
        async def send_json(self, event):
            if self.slow:
                await asyncio.Event().wait()
            self.events.append(event)
        async def close(self):
            self.closed = True
    hub = WebSocketHub(queue_size=1, send_timeout_s=.03)
    slow, fast = Socket(True), Socket()
    slow_client = await hub.connect(slow, "t")
    await hub.connect(fast, "t")
    hub.broadcast({"seq": 1}, "t")
    await asyncio.sleep(.01)
    hub.broadcast({"seq": 2}, "t")
    await asyncio.sleep(.01)
    hub.broadcast({"seq": 3}, "t")
    await asyncio.sleep(.05)
    assert slow_client not in hub.clients
    assert slow.closed
    assert [e["seq"] for e in fast.events] == [1, 2, 3]
    await hub.close()
