// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useRef, useState } from "react";
import { audioUrl } from "@/lib/api";
import { dataOf, formatCost, formatDuration, formatNumber, lastOf, reusedSkillNames, runCreatedAt, runRequest, runStatus } from "@/lib/derive";
import type { RunState } from "@/lib/engine";
import { Usage, usageCost } from "./Usage";
import { Activity } from "./Activity";
import { Outcome, ReviewCard } from "./Approval";
import { CodeBlock, StatusPill } from "./ui";

export function DetectionRun({ run, title, onEdit, busy }: {
  run: RunState;
  title: string;
  onEdit: (text: string) => void;
  busy: boolean;
}) {
  const request = runRequest(run);
  const status = runStatus(run);
  const created = runCreatedAt(run);
  const summaryEvent = lastOf(run, "summary");
  const summary = summaryEvent ? dataOf(summaryEvent, "summary") : null;
  const usageEvent = lastOf(run, "llm_usage");
  const usageTotals = usageEvent ? dataOf(usageEvent, "llm_usage").totals : undefined;
  const voiceEvent = lastOf(run, "voice_ready");
  const reused = [...reusedSkillNames(run)];
  const candidateNames = run.events.filter((event) => event.type === "skill_candidate_ready")
    .map((event) => dataOf(event, "skill_candidate_ready").skill?.name)
    .filter((name): name is string => typeof name === "string" && Boolean(name.trim()));
  const installed = new Set(run.events.filter((event) => event.type === "skill_installed")
    .map((event) => dataOf(event, "skill_installed").skill?.name)
    .filter((name): name is string => typeof name === "string" && Boolean(name.trim())));
  const newSkills = [...new Set([...candidateNames, ...installed])].filter((name) => !reused.includes(name));
  const candidateNote = status === "awaiting_approval" ? "Pending approval"
    : status === "approved" ? "Approval received"
    : status === "rejected" ? "Run rejected"
    : status === "failed" ? "Run failed"
    : "Candidate ready";
  const finished = status === "approved" || status === "rejected" || status === "failed";
  const recipeEvent = [...run.events].reverse().find((event) =>
    (event.type === "awaiting_approval" || event.type === "rule_drafted") && event.data.recipe && typeof event.data.recipe === "object",
  );
  const recipe = recipeEvent?.data.recipe;
  const recipeName = recipe && typeof recipe === "object" && "name" in recipe && typeof recipe.name === "string" ? recipe.name : "rule";

  return (
    <section id="run-view" className="run-detail" aria-labelledby="run-title">
      <div className="run-heading">
        <div>
          <h1 id="run-title">{title}</h1>
          <p id="run-request" className="run-request muted whitespace-pre-wrap">{request ?? "Waiting for the request from the backend."}</p>
        </div>
        {status !== "awaiting_approval" && <div className="shrink-0" role="status"><StatusPill status={status} /></div>}
      </div>
      <RunActions key={run.run_id} request={request} busy={busy} onEdit={onEdit} />
      <div className="run-layout">
        <div className="run-main">
          <article className="panel">
            <div className="panel-header">
              <h2>Activity</h2>
            </div>
            <div className="activity-list">{run.events.length ? <Activity key={run.run_id} run={run} /> : <p className="muted text-sm">Waiting for the first event.</p>}</div>
          </article>
          {summary && typeof summary.text === "string" && summary.text && (
            <article className="panel">
              <div className="panel-header"><h2>Summary</h2></div>
              {summary.text.length > 400 ? (
                <details className="run-summary-output">
                  <summary>Read full summary</summary>
                  <pre tabIndex={0}>{summary.text}</pre>
                </details>
              ) : (
                <p className="run-summary review-summary whitespace-pre-wrap" style={{ overflowWrap: "anywhere" }}>{summary.text}</p>
              )}
              {voiceEvent && <RunAudio key={`${run.run_id}-${voiceEvent.seq}`} runId={run.run_id} />}
            </article>
          )}
          {status === "awaiting_approval" && <ReviewCard key={run.run_id} run={run} />}
          {finished && recipe !== undefined && <article className="panel">
            <div className="panel-header"><h2>Detection rule</h2></div>
            <CodeBlock value={recipe} label={`${recipeName}.json`} />
          </article>}
          <Usage run={run} />
          <Outcome run={run} />
        </div>
        <aside className="run-aside" aria-label="Run details">
          <article className="panel">
            <div className="panel-header"><h2>Details</h2></div>
            <dl className="context-list">
              <div><dt>Created</dt><dd>{created ? <time dateTime={new Date(created).toISOString()}>{new Date(created).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short" })}</time> : "Not available"}</dd></div>
              <div><dt>Run ID</dt><dd className="mono break-all">{run.run_id}</dd></div>
            </dl>
          </article>
          <article className="panel" aria-label="Run statistics">
            <div className="panel-header"><h2>Run statistics</h2></div>
            <dl className="context-list">
              <div><dt>Duration</dt><dd>{formatDuration(summary?.stats?.duration_ms)}</dd></div>
              <div><dt>LLM calls</dt><dd>{formatNumber(usageTotals ? usageTotals.calls : summary?.stats?.llm_calls)}</dd></div>
              <div><dt>Tokens</dt><dd>{formatNumber(usageTotals ? usageTotals.total_tokens : summary?.stats?.tokens_total)}</dd></div>
              <div><dt>Cost (USD)</dt><dd>{usageTotals ? usageCost(usageTotals.cost_usd) : formatCost(summary?.stats?.cost_usd)}</dd></div>
              <div><dt>Skills built</dt><dd>{formatNumber(summary?.stats?.skills_built)}</dd></div>
              <div><dt>Skills reused</dt><dd>{formatNumber(summary?.stats?.skills_reused)}</dd></div>
            </dl>
            {!summary && <p className="muted text-xs">Duration and skill totals appear in the run summary.</p>}
          </article>
          <article className="panel">
            <div className="panel-header"><h2>Skills in this run</h2><span className="nav-count">{reused.length + newSkills.length}</span></div>
            {reused.map((name) => <div className="context-skill" key={`reused-${name}`}><div className="min-w-0"><strong style={{ overflowWrap: "anywhere" }}>{name}</strong><small>Reused</small></div></div>)}
            {newSkills.map((name) => <div className="context-skill" key={`new-${name}`}><div className="min-w-0"><strong style={{ overflowWrap: "anywhere" }}>{name}</strong><small>{installed.has(name) ? "Installed" : candidateNote}</small></div></div>)}
            {!reused.length && !newSkills.length && <p className="muted text-xs leading-relaxed">Tools will appear here as the run progresses.</p>}
          </article>
        </aside>
      </div>
    </section>
  );
}

function RunAudio({ runId }: { runId: string }) {
  const [unavailable, setUnavailable] = useState(false);
  if (unavailable) return null;
  return <audio controls preload="none" aria-label="Audio summary" src={audioUrl(runId)} onError={() => setUnavailable(true)} className="mt-4 h-10 w-full max-w-sm" />;
}

function RunActions({ request, onEdit, busy }: { request: string | null; onEdit: (text: string) => void; busy: boolean }) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const selectionRef = useRef<HTMLTextAreaElement>(null);
  const available = Boolean(request?.trim());

  async function copy() {
    if (!request) return;
    try {
      await navigator.clipboard.writeText(request);
      setCopyState("copied");
    } catch {
      setCopyState("failed");
    }
  }

  return (
    <>
      <div className="run-tools" aria-label="Run actions">
        <button type="button" className="secondary-button" disabled={!available || busy} onClick={() => { if (request) onEdit(request); }}>Edit &amp; rerun</button>
        <button type="button" className="secondary-button" disabled={!available} onClick={() => void copy()}>{copyState === "copied" ? "Copied" : "Copy prompt"}</button>
        {busy && <span className="run-tools-note">Finish the active run before starting another.</span>}
        <span className="sr-only" role="status">{copyState === "copied" ? "Prompt copied." : ""}</span>
      </div>
      {copyState === "failed" && <div className="panel mb-5">
        <p role="alert" className="mb-3 text-sm">Copy is unavailable. Select the prompt below and copy it with your keyboard.</p>
        <label className="sr-only" htmlFor="prompt-copy-fallback">Prompt to copy</label>
        <textarea ref={selectionRef} id="prompt-copy-fallback" rows={4} readOnly value={request ?? ""} onFocus={(event) => event.currentTarget.select()} />
        <button type="button" className="secondary-button mt-2" onClick={() => { selectionRef.current?.focus(); selectionRef.current?.select(); }}>Select prompt</button>
      </div>}
    </>
  );
}
