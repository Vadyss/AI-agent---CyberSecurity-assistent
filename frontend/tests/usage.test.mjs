// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { productionModule } from "./load-production-module.mjs";

const { Usage, usageCost } = productionModule("components/Usage.tsx");
const { Activity } = productionModule("components/Activity.tsx");
const { runStatus } = productionModule("lib/derive.ts");
const totals = { calls: 2, total_tokens: 230, known_tokens: 230, cost_usd: "0.0123456789",
  known_cost_usd: "0.0123456789", unknown_cost_calls: 0, estimated_calls: 0,
  calculated_cost_calls: 0, retries: 1, steps: [{ step: "forge", calls: 2, retries: 1, cost_usd: "0.0123456789", share_percent: "100.0" }] };
const record = { run_id: "run_usage", call_id: 1, step: "forge", model: "<script>bad()</script>",
  iteration: 1, attempt: 1, input_tokens: 100, output_tokens: 15, cached_tokens: 20,
  total_tokens: 115, cache_write_tokens: 0, estimated: false, retry: false,
  duration_ms: 1500, timestamp: "2026-10-09T12:00:00.000Z", cost_usd: "0.006", cost_source: "provider", warning: null, status: "success" };
const ev = (data, seq = 2) => ({ type: "llm_usage", run_id: "run_usage", seq, phase: "forge", timestamp: record.timestamp, message: "Usage recorded.", data });
const initial = { ...ev({}, 1), type: "run_started", phase: "intake", message: "Request received.", data: { request: "Detect spraying" } };
const run = (events) => ({ run_id: "run_usage", events: [initial, ...events], created_at: initial.timestamp, last_seq: events.length + 1 });
const render = (view) => renderToStaticMarkup(React.createElement(Usage, { run: view }));

test("usage table renders live records and server totals, escaping all text", () => {
  const html = render(run([ev({ kind: "call", record, totals, summary: null })]));
  assert.ok(html.includes("100 / 15"));
  assert.ok(html.includes("Cached input tokens</dt><dd>20"));
  assert.ok(html.includes("$0.0060"));
  assert.ok(html.includes("Running total"));
  assert.ok(html.includes("$0.0123456789"), "decimal cost is not truncated or summed in JS");
  assert.ok(html.includes("&lt;script&gt;bad()&lt;/script&gt;"));
  assert.ok(!html.includes("<script>"));
});

test("final usage exposes per-step shares, retries, average and observations", () => {
  const summary = { totals, most_expensive_step: "forge", average_cost_usd: "0.01", average_run_count: 2,
    observations: ["Forge used the most money.", "Retries account for 50% of cost."] };
  const view = run([ev({ kind: "call", record, totals, summary: null }), ev({ kind: "summary", record: null, totals, summary }, 3)]);
  const html = render(view);
  for (const text of ["Cost by step", "100.0%", "Most expensive step: forge", "Retries: 1", "Average across 2 runs", "$0.0100", "Retries account for 50% of cost."])
    assert.ok(html.includes(text), text);
  assert.equal((html.match(/Call 1 details/g) || []).length, 1);
});

test("unknown cost stays unknown and estimates are visible", () => {
  const html = render(run([ev({ kind: "call", record: { ...record, estimated: true, cost_usd: null, warning: "Pricing is unavailable." },
    totals: { ...totals, cost_usd: null, unknown_cost_calls: 1, estimated_calls: 1 }, summary: null })]));
  assert.ok(html.includes("Estimated tokens"));
  assert.ok(html.includes("Known subtotal"));
  assert.ok(html.includes("Cost is unknown for 1 call."));
  assert.ok(html.includes("Pricing is unavailable."));
  assert.equal(usageCost(null), "—");
  assert.equal(usageCost("NaN"), "—");
  assert.equal(usageCost("0"), "$0.0000");
  assert.equal(usageCost("1234567.01"), "$1,234,567.0100");
});

test("usage does not alter run status or duplicate the activity timeline", () => {
  const view = run([ev({ kind: "call", record, totals, summary: null })]);
  assert.equal(runStatus(view), "running");
  const html = renderToStaticMarkup(React.createElement(Activity, { run: view }));
  assert.ok(!html.includes("Usage recorded."));
  assert.ok(render(run([])).includes("Older runs may not include it."));
});


test("a completed run without model calls displays exact zero totals", () => {
  const empty = { ...totals, calls: 0, total_tokens: 0, cost_usd: "0", steps: [], retries: 0 };
  const html = render(run([ev({ kind: "summary", record: null, totals: empty,
    summary: { totals: empty, most_expensive_step: null, average_cost_usd: "0", average_run_count: 1,
      observations: ["No model calls were made."] } })]));
  assert.ok(html.includes("Total: 0 tokens · $0.0000"));
  assert.ok(html.includes("No model calls were made."));
});


test("run statistics use live usage totals and never label a partial cost as the total", () => {
  const { DetectionRun } = productionModule("components/DetectionRun.tsx");
  const view = run([ev({ kind: "call", record, totals: { ...totals, cost_usd: null, unknown_cost_calls: 1 }, summary: null }),
    { ...ev({}, 3), type: "summary", phase: "approval", data: { text: "Rule ready.", stats: {
      duration_ms: 1000, llm_calls: 3, tokens_total: 999, cost_usd: 0.4, skills_built: 0, skills_reused: 1 } } }]);
  const html = renderToStaticMarkup(React.createElement(DetectionRun, { run: view, title: "Run", onEdit() {}, busy: false }));
  assert.ok(html.includes("<dt>Cost (USD)</dt><dd>—</dd>"));
  assert.ok(html.includes("<dt>LLM calls</dt><dd>2</dd>"));
  assert.ok(html.includes("<dt>Tokens</dt><dd>230</dd>"));
  assert.ok(html.includes("Known subtotal: $0.0123456789"));
});


test("comparison uses full usage costs rather than legacy partial subtotals", () => {
  const { Compare } = productionModule("components/Compare.tsx");
  const completed = (id, cost) => ({ ...run([]), run_id: id, events: [initial,
    { ...ev({}, 2), type: "summary", data: { text: "Done", stats: { duration_ms: 1000, llm_calls: 2,
      tokens_total: 230, cost_usd: 0.4, skills_built: 0, skills_reused: 1 } } },
    ev({ kind: "summary", record: null, totals: { ...totals, cost_usd: cost }, summary: null }, 3),
    { ...ev({}, 4), type: "rule_approved", phase: "done", data: { rule_name: "rule" } }] });
  const html = renderToStaticMarkup(React.createElement(Compare, { runs: [completed("run_new", null), completed("run_old", "0.0123456789")] }));
  const costRow = [...html.matchAll(/<tr\b[\s\S]*?<\/tr>/g)].map(value => value[0]).find(value => value.includes("Cost (USD)"));
  assert.ok(costRow.includes("$0.0123456789"));
  assert.ok(costRow.includes("—"));
  assert.ok(!costRow.includes("$0.4000"));
});
