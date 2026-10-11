// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useState } from "react";
import {
  currentPhase,
  dataOf,
  eventTitle,
  formatDuration,
  formatTime,
  isActive,
  PHASE_LABEL,
  runStatus,
} from "@/lib/derive";
import type { RunState } from "@/lib/engine";
import type { RunEvent, SkillInfo } from "@/lib/types";
import { IconAlert, IconCheck, IconChevron, IconCircle, IconHammer, IconShield, IconX } from "./icons";
import { MetricsInline } from "./Metrics";
import { CodeBlock, Tag } from "./ui";

type Tone = "ok" | "bad" | "warn" | "neutral";

function toneOf(e: RunEvent): Tone {
  switch (e.type) {
    case "run_failed":
    case "skill_tests_failed":
      return "bad";
    case "policy_rejected":
      return "warn";
    case "rule_evaluated":
      return dataOf(e, "rule_evaluated").metrics?.passed === true ? "ok" : "bad";
    case "validation_done":
      return dataOf(e, "validation_done").metrics?.passed === true ? "ok" : "bad";
    case "skill_candidate_ready":
    case "skill_installed":
    case "rule_approved":
      return "ok";
    default:
      return "neutral";
  }
}

function StepIcon({ e }: { e: RunEvent }) {
  const tone = toneOf(e);
  const cls = "size-3.5";
  if (e.type === "policy_rejected") return <IconShield className={`${cls} text-warn`} />;
  if (e.type === "forge_started") return <IconHammer className={`${cls} text-subtle`} />;
  if (tone === "ok") return <IconCheck className={`${cls} text-ok`} />;
  if (tone === "bad") return <IconX className={`${cls} text-bad`} />;
  if (tone === "warn") return <IconAlert className={`${cls} text-warn`} />;
  return <IconCircle className={`${cls} text-subtle`} />;
}

const str = (v: unknown) => (typeof v === "string" ? v : "");
const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

function Details({ e }: { e: RunEvent }) {
  switch (e.type) {
    case "plan_ready": {
      const d = dataOf(e, "plan_ready");
      return (
        <div className="space-y-2">
          <ol className="list-decimal space-y-0.5 pl-4 text-muted">
            {arr<string>(d.steps).map((s, i) => (
              <li key={i}>{str(s)}</li>
            ))}
          </ol>
          <div className="flex flex-wrap gap-1">
            {arr<string>(d.skills_needed).map((s, i) => (
              <Tag key={i} mono>
                {str(s)}
              </Tag>
            ))}
          </div>
        </div>
      );
    }
    case "skill_reused":
    case "skill_installed": {
      const s = (e.data as { skill?: Partial<SkillInfo> }).skill;
      if (!s) return null;
      return (
        <p className="text-muted">
          {str(s.description)}{" "}
          <span className="text-subtle">
            · {str(s.kind)} · v{s.version} · {s.origin === "seed" ? "built-in" : "built during a run"}
          </span>
        </p>
      );
    }
    case "capability_missing":
      return (
        <ul className="space-y-0.5 text-muted">
          {arr<{ name: string; description: string }>(dataOf(e, "capability_missing").skills).map((s, i) => (
            <li key={i}>
              <span className="font-mono text-fg">{str(s?.name)}</span> — {str(s?.description)}
            </li>
          ))}
        </ul>
      );
    case "skill_tests_failed": {
      const ex = str(dataOf(e, "skill_tests_failed").error_excerpt);
      return ex ? (
        <pre tabIndex={0} className="max-h-64 overflow-auto rounded-md bg-surface p-3 font-mono text-xs whitespace-pre-wrap break-all text-muted">{ex}</pre>
      ) : null;
    }
    case "skill_candidate_ready": {
      const sha = str(dataOf(e, "skill_candidate_ready").code_sha256);
      return (
        <p className="text-muted">
          Code SHA-256 <span className="font-mono break-all text-subtle">{sha}</span>
        </p>
      );
    }
    case "rule_drafted": {
      const d = dataOf(e, "rule_drafted");
      return (
        <div className="space-y-2">
          {str(d.explanation) && <p className="text-muted">{str(d.explanation)}</p>}
          {d.recipe && <CodeBlock value={d.recipe} label={`${str(d.recipe.name) || "recipe"}.json`} />}
        </div>
      );
    }
    case "rule_evaluated":
      return <MetricsInline metrics={dataOf(e, "rule_evaluated").metrics} />;
    case "validation_done":
      return <MetricsInline metrics={dataOf(e, "validation_done").metrics} />;
    case "policy_rejected": {
      const d = dataOf(e, "policy_rejected");
      return (
        <ul className="space-y-1">
          {arr<{ code: string; detail: string }>(d.violations).map((v, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-2">
              <Tag mono>{str(v?.code)}</Tag>
              <span className="text-muted">{str(v?.detail)}</span>
            </li>
          ))}
        </ul>
      );
    }
    case "run_failed": {
      const r = str(dataOf(e, "run_failed").reason);
      return r ? <p className="text-muted">{r}</p> : null;
    }
    case "rule_rejected": {
      const r = str(dataOf(e, "rule_rejected").reason);
      return r ? <p className="text-muted">{r}</p> : null;
    }
    case "rule_approved": {
      const c = str(dataOf(e, "rule_approved").comment);
      return c ? <p className="text-muted">{c}</p> : null;
    }
    default:
      return null;
  }
}

const HAS_DETAILS = new Set([
  "plan_ready",
  "skill_reused",
  "skill_installed",
  "capability_missing",
  "skill_tests_failed",
  "skill_candidate_ready",
  "rule_drafted",
  "rule_evaluated",
  "validation_done",
  "policy_rejected",
  "run_failed",
  "rule_rejected",
  "rule_approved",
]);

function Step({ e }: { e: RunEvent }) {
  const [open, setOpen] = useState(false);
  const expandable = HAS_DETAILS.has(e.type);
  const title = (
    <>
      <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center">
        <StepIcon e={e} />
      </span>
      <span className="min-w-0 flex-1 text-left" style={{ overflowWrap: "anywhere" }}>
        <span>{eventTitle(e)}</span>
        {e.message && e.message !== eventTitle(e) && (
          <span className="mt-0.5 block text-xs text-muted whitespace-pre-wrap">{e.message}</span>
        )}
      </span>
      <span className="activity-meta">
        {e.phase && (
          <span className="text-xs text-subtle" aria-label="Event phase">
            {Object.hasOwn(PHASE_LABEL, e.phase) ? PHASE_LABEL[e.phase] : e.phase}
          </span>
        )}
        <time className="text-xs text-subtle tabular-nums" dateTime={e.timestamp}>
          {formatTime(e.timestamp)}
        </time>
        {expandable && <IconChevron className={`size-3.5 shrink-0 text-subtle ${open ? "rotate-90" : ""}`} />}
      </span>
    </>
  );
  return (
    <li>
      {expandable ? (
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className="activity-step-head flex w-full items-start gap-2 rounded-md px-2 py-2 text-sm hover:bg-surface"
        >
          {title}
        </button>
      ) : (
        <div className="activity-step-head flex items-start gap-2 px-2 py-2 text-sm">{title}</div>
      )}
      {open && <div className="mt-1 mb-2 ml-8 mr-2 text-sm">{<Details e={e} />}</div>}
    </li>
  );
}

// Collapsible timeline of backend events.
export function Activity({ run }: { run: RunState }) {
  const status = runStatus(run);
  const working = status === "running";
  const [open, setOpen] = useState(isActive(status));
  const steps = run.events.filter((event) => event.type !== "llm_usage");
  const phase = currentPhase(run);

  const summary = lastSummary(run);
  const header = working ? (
    <span>
      {phase ? `${PHASE_LABEL[phase]}…` : "Working…"}
    </span>
  ) : (
    <span>
      {summary ? `${formatDuration(summary)} · ` : ""}{steps.length} updates
    </span>
  );

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-1.5 text-sm text-muted hover:text-fg"
      >
        {header}
        <IconChevron className={`size-3.5 ${open ? "rotate-90" : ""}`} />
      </button>
      {open && (
        <ol className="mt-2 border-l border-line pl-2">
          {steps.map((e) => (
            <Step key={e.seq} e={e} />
          ))}
        </ol>
      )}
    </div>
  );
}

function lastSummary(run: RunState): number | null {
  for (let i = run.events.length - 1; i >= 0; i--) {
    const e = run.events[i];
    if (e.type === "summary") {
      const ms = dataOf(e, "summary").stats?.duration_ms;
      return typeof ms === "number" ? ms : null;
    }
  }
  return null;
}
