// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { productionModule } from "./load-production-module.mjs";

const { Compare } = productionModule("components/Compare.tsx");
const { ReviewCard, Outcome } = productionModule("components/Approval.tsx");
const { DetectionRun } = productionModule("components/DetectionRun.tsx");
const { CodeBlock } = productionModule("components/ui.tsx");
const { formatCost, formatNumber } = productionModule("lib/derive.ts");
const metric = { true_positives: 9, false_positives: 0, false_negatives: 0,
  precision: 1, recall: 1, passed: true, thresholds: { min_precision: 0.9, min_recall: 0.8 } };
const text = "<script>analyst-authored text stays literal</script>";

function run(id, stats, status = "awaiting_approval", newSkills = []) {
  const events = [];
  function append(type, phase, data) {
    events.push({ type, phase, data, run_id: id, seq: events.length + 1,
      timestamp: "2026-10-09T10:00:00.000Z", message: "Backend message." });
  }
  append("run_started", "intake", { request: text });
  append("summary", "approval", { text: "API-provided summary.", stats });
  append("awaiting_approval", "approval", { recipe: { name: "api_provided_rule", steps: [] },
    metrics_tuning: metric, metrics_validation: metric, new_skills: newSkills });
  if (status === "approved") append("rule_approved", "done", { rule_name: "api_provided_rule", comment: null });
  if (status === "rejected") append("rule_rejected", "done", { reason: "Analyst decision." });
  return { run_id: id, created_at: events[0].timestamp, last_seq: events.length, events };
}

const stats = { duration_ms: 9500, llm_calls: 7, tokens_total: 12345, cost_usd: 0.0123,
  skills_built: 0, skills_reused: 2 };
const render = (Component, props) => renderToStaticMarkup(React.createElement(Component, props));

function statistic(html, label, value) {
  assert.ok(html.includes(`<dt>${label}</dt><dd>${value}</dd>`), `${label} must come from summary.stats`);
}

test("current run panels show API-provided tokens, USD cost and summary statistics", () => {
  const html = render(DetectionRun, { run: run("run_abcdefgh", stats), title: "API run", busy: true, onEdit() {} });
  statistic(html, "LLM calls", "7");
  statistic(html, "Tokens", "12,345");
  statistic(html, "Cost (USD)", "$0.0123");
  statistic(html, "Skills built", "0");
  statistic(html, "Skills reused", "2");
  assert.ok(html.includes("API-provided summary."));
  assert.ok(html.includes("&lt;script&gt;analyst-authored text stays literal&lt;/script&gt;"));
  assert.ok(!html.includes("<script>"));
});

test("unknown usage is an em dash in both current run and comparison, never zero", () => {
  const unknown = { ...stats, tokens_total: null, cost_usd: null };
  const html = render(DetectionRun, { run: run("run_abcdefgh", unknown), title: "API run", busy: true, onEdit() {} });
  statistic(html, "Tokens", "—");
  statistic(html, "Cost (USD)", "—");
  const comparison = render(Compare, { runs: [run("run_bcdefghi", unknown, "approved"), run("run_abcdefgh", stats, "approved")] });
  const costRow = [...comparison.matchAll(/<tr\b[\s\S]*?<\/tr>/g)].map((x) => x[0]).find((x) => x.includes("Cost (USD)"));
  assert.ok(costRow.includes("$0.0123"));
  assert.ok(costRow.includes("—"));
  assert.ok(!costRow.includes("$0.0000"));
  assert.equal(formatCost(undefined), "—", "older summaries without additive cost field remain compatible");
});

test("comparison uses two completed summaries and keeps the older run on the left", () => {
  const comparison = render(Compare, { runs: [
    run("run_cdefghij", { ...stats, cost_usd: 0.99 }, "awaiting_approval"),
    run("run_bcdefghi", { ...stats, cost_usd: 0.0023, tokens_total: 2345 }, "rejected"),
    run("run_abcdefgh", stats, "approved"),
  ] });
  const costRow = [...comparison.matchAll(/<tr\b[\s\S]*?<\/tr>/g)].map((x) => x[0]).find((x) => x.includes("Cost (USD)"));
  assert.ok(costRow.indexOf("$0.0123") < costRow.indexOf("$0.0023"));
  assert.ok(!comparison.includes("$0.9900"), "unfinished run must not replace completed comparison data");
  assert.ok(comparison.includes("12,345"));
  assert.ok(comparison.includes("2,345"));
});

test("approval stays available with no new skills and renders the exact reused-tools notice", () => {
  const html = render(ReviewCard, { run: run("run_abcdefgh", stats) });
  assert.ok(html.includes("Approve detection rule"));
  assert.ok(html.includes("No new tools built in this run (reused existing tools)"));
  assert.ok(html.includes("api_provided_rule"));
  assert.ok(html.includes("Approve and install"));
  assert.ok(!html.includes("disabled="), "an empty new_skills array must not disable approval");
  assert.ok(html.includes("Tuning set"));
  assert.ok(html.includes("Validation set"));
});

test("the approval action is shown once without duplicate approval badges or context notes", () => {
  const html = render(DetectionRun, { run: run("run_abcdefgh", stats), title: "API run", busy: true, onEdit() {} });
  assert.equal((html.match(/Approve detection rule/g) ?? []).length, 1);
  assert.equal((html.match(/Approve and install/g) ?? []).length, 1);
  assert.ok(!html.includes("Needs review"));
  assert.ok(!html.includes("Human required"));
  assert.ok(!html.includes("Approval is required to install"));
  assert.ok(html.includes("run_abcdefgh"), "the useful run identifier remains available in Details");
});

test("rule JSON starts collapsed and retains plain text, copy and download controls", () => {
  const html = render(CodeBlock, { value: { explanation: text }, label: "api_rule.json" });
  const details = html.match(/<details\b[^>]*>/)?.[0];
  assert.ok(details, "a native details disclosure provides keyboard-accessible expansion");
  assert.ok(!/\bopen(?:=|\s|>)/.test(details), "long JSON output starts collapsed");
  assert.ok(html.includes("<summary"));
  assert.ok(html.includes("api_rule.json"));
  assert.ok(html.includes("Copy</button>"));
  assert.ok(html.includes("Download JSON</button>"));
  assert.ok(html.includes("&lt;script&gt;analyst-authored text stays literal&lt;/script&gt;"));
  assert.ok(!html.includes("<script>"));
});

test("long summaries start collapsed and keep the full API text escaped in a keyboard-readable block", () => {
  const value = run("run_abcdefgh", stats);
  const longText = `${text} ${"Detailed backend result. ".repeat(30)}`;
  value.events.find((event) => event.type === "summary").data.text = longText;
  const html = render(DetectionRun, { run: value, title: "API run", busy: true, onEdit() {} });
  const output = html.match(/<details class="run-summary-output">([\s\S]*?)<\/details>/);
  assert.ok(output, "a long summary uses a closed native disclosure");
  assert.ok(output[1].includes("<summary>Read full summary</summary>"));
  assert.ok(output[1].includes('<pre tabindex="0">'));
  const escaped = longText.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
  assert.ok(output[1].includes(escaped), "the complete API summary remains available without truncation");
  assert.ok(!html.includes("<script>"));
});

test("approval shows new skill descriptions literally and hides after terminal events", () => {
  const newSkills = [{ name: "api_provided_skill", kind: "aggregation", description: text }];
  const html = render(ReviewCard, { run: run("run_abcdefgh", stats, "awaiting_approval", newSkills) });
  assert.ok(html.includes("api_provided_skill"));
  assert.ok(html.includes("&lt;script&gt;analyst-authored text stays literal&lt;/script&gt;"));
  assert.ok(!html.includes("No new tools built"));
  assert.equal(render(ReviewCard, { run: run("run_abcdefgh", stats, "approved") }), "");
  assert.equal(render(ReviewCard, { run: run("run_abcdefgh", stats, "rejected") }), "");
});

test("US currency formatting preserves small API costs and rejects unavailable values", () => {
  assert.equal(formatCost(0), "$0.0000");
  assert.equal(formatCost(0.0123), "$0.0123");
  assert.equal(formatCost(0.000001), "$0.000001");
  assert.equal(formatCost(null), "—");
  assert.equal(formatCost(NaN), "—");
  assert.equal(formatCost(-1), "—");
  assert.equal(formatNumber(null), "—");
  assert.equal(formatNumber(12345), "12,345");
});


test("failed outcome exposes the protocol reason code and literal backend reason", () => {
  const value = run("run_abcdefgh", stats);
  value.events.push({ type: "run_failed", run_id: value.run_id, seq: value.events.length + 1,
    phase: "done", timestamp: "2026-10-09T10:00:00.000Z", message: "Run failed.",
    data: { reason_code: "LLM_ERROR", reason: "<img src=x onerror=alert(1)>" } });
  const html = render(Outcome, { run: value });
  assert.ok(html.includes("LLM_ERROR"));
  assert.ok(html.includes("LLM API error"));
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"));
  assert.ok(!html.includes("<img "));
  assert.equal(render(ReviewCard, { run: value }), "");
});

test("comparison includes failed completed runs with summaries and skips failures without statistics", () => {
  const failed = run("run_bcdefghi", { ...stats, cost_usd: 0.0023 });
  failed.events.push({ type: "run_failed", run_id: failed.run_id, seq: failed.events.length + 1,
    phase: "done", timestamp: "2026-10-09T10:00:00.000Z", message: "Run failed.",
    data: { reason_code: "INTERNAL_ERROR", reason: "Failure after summary." } });
  const withoutSummary = { ...failed, run_id: "run_cdefghij",
    events: failed.events.filter((event) => event.type !== "summary") };
  const comparison = render(Compare, { runs: [withoutSummary, failed, run("run_abcdefgh", stats, "approved")] });
  const costRow = [...comparison.matchAll(/<tr\b[\s\S]*?<\/tr>/g)].map((x) => x[0]).find((x) => x.includes("Cost (USD)"));
  assert.ok(costRow, "failed run with a summary must qualify as completed comparison data");
  assert.ok(costRow.indexOf("$0.0123") < costRow.indexOf("$0.0023"));
  assert.ok(!comparison.includes("Finish two runs"));
});
