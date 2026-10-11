// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import assert from "node:assert/strict";
import test from "node:test";
import { productionModule } from "./load-production-module.mjs";

const { ApiError } = productionModule("lib/api.ts");
const settle = () => new Promise((resolve) => setImmediate(resolve));
const event = (seq, type = "run_started") => ({
  run_id: "run_abcdefgh", seq, type, phase: "intake",
  timestamp: "2026-10-09T10:00:00.000Z", message: "Událost.", data: {},
});

function fixture(t, overrides = {}) {
  const calls = [];
  const sockets = [];
  const api = {
    listRuns: async () => { calls.push("runs"); return { runs: [] }; },
    events: async (id, seq) => { calls.push([id, seq]); return { events: [] }; },
    skills: async () => { calls.push("skills"); return { skills: [] }; },
    ...overrides,
  };
  const original = globalThis.WebSocket;
  globalThis.WebSocket = class {
    constructor() { sockets.push(this); }
    close() {}
    open() { this.onopen(); }
    receive(value) { this.onmessage({ data: JSON.stringify(value) }); }
  };
  const { Engine } = productionModule("lib/engine.ts", { "./api": { api, ApiError, wsUrl: () => "ws://localhost/ws" } });
  const engine = new Engine();
  t.after(() => { engine.stop(); globalThis.WebSocket = original; });
  engine.start();
  return { engine, calls, sockets };
}

test("history waits for the WebSocket handshake and preserves events arriving during hydration", async (t) => {
  let resolveRuns;
  const { engine, calls, sockets } = fixture(t, {
    listRuns: () => { calls.push("runs"); return new Promise((resolve) => { resolveRuns = resolve; }); },
    events: async () => ({ events: [event(1)] }),
  });
  assert.equal(sockets.length, 1);
  assert.deepEqual(calls, [], "history must not be requested while the socket is still connecting");
  sockets[0].open();
  sockets[0].receive(event(2, "plan_ready"));
  resolveRuns({ runs: [{ run_id: "run_abcdefgh", created_at: event(1).timestamp }] });
  await settle();
  const run = engine.getSnapshot().runs.run_abcdefgh;
  assert.deepEqual(run.events.map((e) => e.seq), [1, 2]);
  assert.equal(run.last_seq, 2);
  assert.equal(engine.getSnapshot().loaded, true);
});

test("event gaps are recovered and duplicate or unknown events do not corrupt run state", async (t) => {
  const { engine, calls, sockets } = fixture(t, {
    events: async (id, seq) => { calls.push([id, seq]); return { events: [event(1), event(2, "future_event"), event(3, "plan_ready")] }; },
  });
  sockets[0].open();
  await settle();
  sockets[0].receive(event(3, "plan_ready"));
  await settle();
  sockets[0].receive(event(3, "plan_ready"));
  const run = engine.getSnapshot().runs.run_abcdefgh;
  assert.deepEqual(run.events.map((e) => e.seq), [1, 2, 3]);
  assert.equal(run.last_seq, 3);
  assert.deepEqual(calls.find(Array.isArray), ["run_abcdefgh", 0]);
});

test("a failed gap request is retried even when the WebSocket stays connected", async (t) => {
  let attempts = 0;
  const { engine, sockets } = fixture(t, {
    events: async () => {
      if (++attempts === 1) throw new Error("temporary outage");
      return { events: [event(1), event(2), event(3)] };
    },
  });
  sockets[0].open();
  await settle();
  t.mock.timers.enable({ apis: ["setTimeout"] });
  sockets[0].receive(event(3));
  await settle();
  assert.equal(engine.getSnapshot().runs.run_abcdefgh.last_seq, 0);
  t.mock.timers.tick(3000);
  await settle();
  assert.equal(attempts, 2);
  assert.equal(engine.getSnapshot().runs.run_abcdefgh.last_seq, 3);
});

test("backend restart removes forgotten runs while installed skills are refreshed", async (t) => {
  let restarted = false;
  const { engine, sockets } = fixture(t, {
    listRuns: async () => ({ runs: restarted ? [] : [{ run_id: "run_abcdefgh", created_at: event(1).timestamp }] }),
    events: async () => {
      if (restarted) throw new ApiError(404, "RUN_NOT_FOUND", "Běh neexistuje.");
      return { events: [event(1)] };
    },
    skills: async () => ({ skills: [{ name: "ssh_parser", version: 1 }] }),
  });
  sockets[0].open();
  await settle();
  assert.equal(Object.keys(engine.getSnapshot().runs).length, 1);
  restarted = true;
  await engine.resync();
  assert.deepEqual(engine.getSnapshot().runs, {});
  assert.equal(engine.getSnapshot().skills[0].name, "ssh_parser");
});

test("reconnect recovers missed terminal events from the last contiguous cursor and refreshes skills", async (t) => {
  let afterReconnect = false;
  let skillRequests = 0;
  const cursors = [];
  const { engine, sockets } = fixture(t, {
    listRuns: async () => ({ runs: [{ run_id: "run_abcdefgh", created_at: event(1).timestamp, status: "approved" }] }),
    events: async (_, cursor) => {
      cursors.push(cursor);
      return { events: afterReconnect ? [event(2, "rule_approved")] : [event(1)] };
    },
    skills: async () => { skillRequests++; return { skills: afterReconnect ? [{ name: "new_api_skill", version: 1 }] : [] }; },
  });
  const { runStatus } = productionModule("lib/derive.ts");
  sockets[0].open();
  await settle();
  assert.equal(runStatus(engine.getSnapshot().runs.run_abcdefgh), "running", "HTTP status must not replace event-derived state");
  t.mock.timers.enable({ apis: ["setTimeout"] });
  sockets[0].onclose();
  assert.equal(engine.getSnapshot().connection, "reconnecting");
  afterReconnect = true;
  t.mock.timers.tick(1000);
  assert.equal(sockets.length, 2);
  sockets[1].open();
  await settle();
  assert.deepEqual(cursors, [0, 1]);
  assert.equal(runStatus(engine.getSnapshot().runs.run_abcdefgh), "approved");
  assert.equal(engine.getSnapshot().skills[0].name, "new_api_skill");
  assert.equal(skillRequests, 2);
  assert.equal(engine.getSnapshot().connection, "open");
});

test("history sync displays the backend error and retries without discarding buffered events", async (t) => {
  let attempts = 0;
  const { engine, sockets } = fixture(t, {
    listRuns: async () => {
      if (++attempts === 1) throw new ApiError(503, "INTERNAL_ERROR", "Backend is temporarily unavailable.");
      return { runs: [{ run_id: "run_abcdefgh", created_at: event(1).timestamp }] };
    },
    events: async () => ({ events: [event(1)] }),
  });
  t.mock.timers.enable({ apis: ["setTimeout"] });
  sockets[0].open();
  sockets[0].receive(event(2, "plan_ready"));
  await settle();
  assert.equal(engine.getSnapshot().loaded, false);
  assert.equal(engine.getSnapshot().syncError, "Backend is temporarily unavailable.");
  t.mock.timers.tick(3000);
  await settle();
  assert.equal(engine.getSnapshot().syncError, null);
  assert.equal(engine.getSnapshot().loaded, true);
  assert.deepEqual(engine.getSnapshot().runs.run_abcdefgh.events.map((value) => value.seq), [1, 2]);
});
