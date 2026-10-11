// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import { dataOf, formatDuration, formatNumber } from "@/lib/derive";
import type { RunState } from "@/lib/engine";
import type { UsageRecord } from "@/lib/types";

// Keep decimal money strings intact; no client-side monetary arithmetic.
export function usageCost(value: string | null | undefined): string {
  if (typeof value !== "string" || !/^\d+(?:\.\d+)?$/.test(value)) return "—";
  const [whole, decimals = ""] = value.split(".");
  return `$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${decimals.padEnd(4, "0")}`;
}

export function Usage({ run }: { run: RunState }) {
  const events = run.events.filter((event) => event.type === "llm_usage");
  const latest = events.at(-1);
  const data = latest ? dataOf(latest, "llm_usage") : null;
  const records = events.map((event) => dataOf(event, "llm_usage").record)
    .filter((record): record is UsageRecord => Boolean(record && typeof record.call_id === "number"));
  const totals = data?.totals;
  const summary = data?.summary;
  const warnings = [...new Set(records.map((record) => record.warning).filter(Boolean))];
  return (
    <article className="panel usage-panel" aria-label="Model usage">
      <div className="panel-header"><h2>Model usage</h2></div>
      {!records.length && !totals ? <p className="muted text-sm">Usage appears after each model call. Older runs may not include it.</p> : <>
        {!records.length && <p className="muted text-sm">No model calls were made.</p>}
        {records.length > 0 && <div className="usage-table-wrap" tabIndex={0} role="region" aria-label="Usage by model call">
          <table className="usage-table">
            <thead><tr><th scope="col">Step</th><th scope="col">Model</th><th scope="col">Tokens in / out</th><th scope="col">Cost (USD)</th></tr></thead>
            <tbody>{records.map((record) => <tr key={record.call_id}>
              <th scope="row">{record.step.replaceAll("_", " ")}<small>Iteration {record.iteration} · attempt {record.attempt}{record.retry ? " · retry" : ""}{record.status === "error" ? " · failed" : ""}</small></th>
              <td><span className="mono">{record.model}</span><details><summary>Call {record.call_id} details</summary><dl>
                <dt>Cached input tokens</dt><dd>{formatNumber(record.cached_tokens)}</dd>
                <dt>Cache write tokens</dt><dd>{formatNumber(record.cache_write_tokens)}</dd>
                <dt>Duration</dt><dd>{formatDuration(record.duration_ms)}</dd>
                <dt>Time</dt><dd><time dateTime={record.timestamp}>{record.timestamp}</time></dd>
                <dt>Cost source</dt><dd>{record.cost_source}</dd>
              </dl></details></td>
              <td className="tabular-nums">{formatNumber(record.input_tokens)} / {formatNumber(record.output_tokens)}{record.estimated && <small>Estimated tokens</small>}</td>
              <td className="tabular-nums">{usageCost(record.cost_usd)}{record.cost_source === "pricing" && <small>Calculated estimate</small>}{record.cost_source === "mock" && <small>Mock · no charge</small>}</td>
            </tr>)}</tbody>
          </table>
        </div>}
        <p className="usage-total" role="status">{summary ? "Total" : "Running total"}: {formatNumber(totals?.total_tokens)} tokens · {usageCost(totals?.cost_usd)}{Boolean(totals?.estimated_calls) && " (includes estimated tokens)"}{Boolean(totals?.calculated_cost_calls) && " (includes calculated cost)"}</p>
        {Boolean(totals?.unknown_cost_calls) && <p className="muted text-sm">Known subtotal: {usageCost(totals?.known_cost_usd)}. Cost is unknown for {totals?.unknown_cost_calls} {totals?.unknown_cost_calls === 1 ? "call" : "calls"}.</p>}
        {warnings.map((warning) => <p key={warning} className="text-warn text-xs">{warning}</p>)}
        <p className="muted text-xs">Inference cost excludes Apify rounding, plan markup and relay hosting.</p>
      </>}
      {summary && <div className="usage-summary">
        <h3>Cost by step</h3>
        <table className="usage-table"><thead><tr><th scope="col">Step</th><th scope="col">Cost (USD)</th><th scope="col">Share</th><th scope="col">Retries</th></tr></thead>
          <tbody>{summary.totals.steps.map((step) => <tr key={step.step}><th scope="row">{step.step.replaceAll("_", " ")}</th><td>{usageCost(step.cost_usd)}</td><td>{step.share_percent === null ? "—" : `${step.share_percent}%`}</td><td>{step.retries}</td></tr>)}</tbody>
        </table>
        <p className="muted text-sm">Most expensive step: {summary.most_expensive_step?.replaceAll("_", " ") ?? "—"}. Retries: {summary.totals.retries}.</p>
        <p className="muted text-sm">Average across {summary.average_run_count} runs with available costs (including estimates): {usageCost(summary.average_cost_usd)}.</p>
        <ul className="usage-observations">{summary.observations.map((text, index) => <li key={index}>{text}</li>)}</ul>
      </div>}
    </article>
  );
}
