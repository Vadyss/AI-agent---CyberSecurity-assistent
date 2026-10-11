// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { productionModule } from "./load-production-module.mjs";

const { Activity } = productionModule("components/Activity.tsx");
const { formatRatio, runStatus } = productionModule("lib/derive.ts");
const strings = [
  "<img src=x onerror=alert(1)>",
  "<script>alert(1)</script>",
  "**tučně** [odkaz](javascript:alert(1))",
  "A".repeat(300),
];

function run(message) {
  return {
    run_id: "run_abcdefgh", created_at: "2026-10-09T10:00:00.000Z", last_seq: 1,
    events: [{ type: "run_started", run_id: "run_abcdefgh", seq: 1,
      phase: "intake", timestamp: "2026-10-09T10:00:00.000Z", message,
      data: { request: message, future_field: true } }],
  };
}

for (const message of strings) {
  test(`timeline includes the backend message as escaped plain text: ${message.slice(0, 40)}`, () => {
    const html = renderToStaticMarkup(React.createElement(Activity, { run: run(message) }));
    const escaped = message.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    assert.ok(html.includes(escaped), "known event types must display their API message, too");
    assert.ok(!html.includes("<img "));
    assert.ok(!html.includes("<script>"));
    assert.ok(!html.includes('href="javascript:'));
    assert.ok(html.includes('overflow-wrap:anywhere'));
    assert.ok(html.includes('dateTime="2026-10-09T10:00:00.000Z"'));
  });
}

test("unknown event types and fields render safely without changing the run status", () => {
  const value = run("Začátek.");
  value.events.push({ ...value.events[0], seq: 2, type: "future_event", message: strings[0], data: { nested: { arbitrary: true } } });
  assert.equal(runStatus(value), "running");
  const html = renderToStaticMarkup(React.createElement(Activity, { run: value }));
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
});

test("nullable precision and recall display an em dash", () => {
  assert.equal(formatRatio(null), "—");
  assert.equal(formatRatio(undefined), "—");
  assert.equal(formatRatio(0.85), "85%");
});

test("event timestamps are visible without hover and do not animate into view", () => {
  const html = renderToStaticMarkup(React.createElement(Activity, { run: run("Run started.") }));
  const time = html.match(/<time\b[^>]*>[\s\S]*?<\/time>/)?.[0];
  assert.ok(time);
  assert.ok(time.includes('dateTime="2026-10-09T10:00:00.000Z"'));
  assert.ok(!time.includes("opacity-0"));
  assert.ok(!time.includes("group-hover"));
  assert.ok(!html.includes("animate-in"));
});

test("each timeline event shows its own phase and unknown phase labels remain plain text", () => {
  const value = run("Začátek.");
  value.events.push(
    { ...value.events[0], seq: 2, type: "plan_ready", phase: "plan", message: "Plán.", data: {} },
    { ...value.events[0], seq: 3, type: "future_event", phase: "<future phase>", message: "Budoucí krok." },
    { ...value.events[0], seq: 4, type: "future_event", phase: "__proto__", message: "Jiný krok." },
  );
  const html = renderToStaticMarkup(React.createElement(Activity, { run: value }));
  const rows = [...html.matchAll(/<li\b[\s\S]*?<\/li>/g)].map((match) => match[0]);
  assert.equal(rows.length, 4);
  for (const [index, phase] of ["Intake", "Plan", "&lt;future phase&gt;", "__proto__"].entries()) {
    assert.ok(rows[index].includes(`aria-label="Event phase">${phase}</span>`));
  }
  assert.ok(!html.includes("<future phase>"));
});
