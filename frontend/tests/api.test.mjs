// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import assert from "node:assert/strict";
import test from "node:test";
import { productionModule } from "./load-production-module.mjs";

function client(t, base) {
  const previous = process.env.NEXT_PUBLIC_API_BASE;
  process.env.NEXT_PUBLIC_API_BASE = base;
  const value = productionModule("lib/api.ts");
  t.after(() => {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_API_BASE;
    else process.env.NEXT_PUBLIC_API_BASE = previous;
  });
  return value;
}

test("HTTP and WebSocket share the configured backend origin and preserve protocol paths", async (t) => {
  const { api, apiUrl, wsUrl, audioUrl } = client(t, "https://api.example.test///");
  assert.equal(apiUrl("/health"), "https://api.example.test/health");
  assert.equal(wsUrl(), "wss://api.example.test/ws");
  assert.equal(audioUrl("run_abcdefgh"), "https://api.example.test/runs/run_abcdefgh/audio");
  const original = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return Response.json({ run_id: "run_abcdefgh", status: "running" }, { status: 202 });
  };
  t.after(() => { globalThis.fetch = original; });
  const userText = "<script>Český uživatelský text remains unchanged</script>";
  await api.createRun(userText);
  await api.events("run_abcdefgh", 7);
  await api.approve("run_abcdefgh", "Analyst comment");
  await api.reject("run_abcdefgh", "Analyst reason");
  assert.equal(calls[0].url, "https://api.example.test/runs");
  assert.deepEqual(JSON.parse(calls[0].options.body), { request: userText });
  assert.deepEqual(calls[0].options.headers, { "Content-Type": "application/json" });
  assert.equal(calls[1].url, "https://api.example.test/runs/run_abcdefgh/events?after_seq=7");
  assert.deepEqual(JSON.parse(calls[2].options.body), { comment: "Analyst comment" });
  assert.deepEqual(JSON.parse(calls[3].options.body), { reason: "Analyst reason" });
  assert.ok(calls.every(({ options }) => !options.headers?.Authorization));
});

test("structured errors preserve backend status, protocol code and literal message", async (t) => {
  const { api, ApiError } = client(t, "http://127.0.0.1:8000");
  const original = globalThis.fetch;
  const message = "<img src=x onerror=alert(1)>";
  globalThis.fetch = async () => Response.json({ error: { code: "RUN_ALREADY_ACTIVE", message } }, { status: 409 });
  t.after(() => { globalThis.fetch = original; });
  await assert.rejects(api.createRun("Detect SSH abuse."), (error) =>
    error instanceof ApiError && error.status === 409 && error.code === "RUN_ALREADY_ACTIVE" && error.message === message);
});

test("transport failure returns an English network error without leaking request content", async (t) => {
  const { api, ApiError, wsUrl } = client(t, "http://127.0.0.1:8000");
  assert.equal(wsUrl(), "ws://127.0.0.1:8000/ws");
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error("prompt content must not become the client error"); };
  t.after(() => { globalThis.fetch = original; });
  await assert.rejects(api.health(), (error) => error instanceof ApiError && error.code === "NETWORK_ERROR" && error.message === "Can't reach the backend.");
});
