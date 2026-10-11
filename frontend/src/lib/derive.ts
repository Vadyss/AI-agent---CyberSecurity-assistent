// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
// Values derived from events (contract chapters 11 and 12.5).
import type { RunState } from "./engine";
import {
  PHASES,
  type EventDataMap,
  type KnownEventType,
  type Phase,
  type RunEvent,
  type RunStatus,
  type SkillInfo,
} from "./types";

export function dataOf<K extends KnownEventType>(e: RunEvent, type: K): Partial<EventDataMap[K]> {
  return (e.type === type ? e.data : {}) as Partial<EventDataMap[K]>;
}

export function lastOf<K extends KnownEventType>(run: RunState, type: K): RunEvent | undefined {
  for (let i = run.events.length - 1; i >= 0; i--) {
    if (run.events[i].type === type) return run.events[i];
  }
  return undefined;
}

// Contract 11.7
const STATUS_BY_EVENT: Record<string, RunStatus> = {
  run_started: "running",
  awaiting_approval: "awaiting_approval",
  rule_approved: "approved",
  rule_rejected: "rejected",
  run_failed: "failed",
};

export function runStatus(run: RunState): RunStatus | null {
  let status: RunStatus | null = null;
  for (const e of run.events) {
    const s = STATUS_BY_EVENT[e.type];
    if (s) status = s;
  }
  return status;
}

export function isActive(status: RunStatus | null) {
  return status === "running" || status === "awaiting_approval";
}

export function currentPhase(run: RunState): Phase | null {
  let idx = -1;
  for (const e of run.events) {
    const i = PHASES.indexOf(e.phase as Phase);
    if (i > idx) idx = i;
  }
  return idx >= 0 ? PHASES[idx] : null;
}

export function phasesSeen(run: RunState): Set<string> {
  return new Set(run.events.map((e) => e.phase));
}

export function runRequest(run: RunState): string | null {
  const e = run.events.find((x) => x.type === "run_started");
  const r = e ? dataOf(e, "run_started").request : undefined;
  return typeof r === "string" ? r : null;
}

export function runCreatedAt(run: RunState): number {
  const t = Date.parse(run.created_at ?? run.events[0]?.timestamp ?? "");
  return Number.isNaN(t) ? 0 : t;
}

export function sortRuns(runs: Record<string, RunState>): RunState[] {
  return Object.values(runs).sort((a, b) => runCreatedAt(b) - runCreatedAt(a));
}

// Skills: base list from GET /skills plus every skill_installed event.
export function installedSkills(base: SkillInfo[], runs: Record<string, RunState>): SkillInfo[] {
  const byName = new Map<string, SkillInfo>();
  for (const s of base) if (s && typeof s.name === "string") byName.set(s.name, s);
  for (const run of Object.values(runs)) {
    for (const e of run.events) {
      if (e.type !== "skill_installed") continue;
      const s = dataOf(e, "skill_installed").skill;
      if (!s || typeof s.name !== "string") continue;
      const prev = byName.get(s.name);
      if (!prev || (s.version ?? 0) >= (prev.version ?? 0)) byName.set(s.name, s);
    }
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name, "en-US"));
}

export function reusedSkillNames(run: RunState | undefined): Set<string> {
  const names = new Set<string>();
  if (!run) return names;
  for (const e of run.events) {
    if (e.type !== "skill_reused") continue;
    const s = dataOf(e, "skill_reused").skill;
    if (s && typeof s.name === "string") names.add(s.name);
  }
  return names;
}

export function candidateSkills(run: RunState | undefined): SkillInfo[] {
  if (!run) return [];
  const status = runStatus(run);
  if (status !== "running" && status !== "awaiting_approval") return [];
  const out: SkillInfo[] = [];
  for (const e of run.events) {
    if (e.type !== "skill_candidate_ready") continue;
    const s = dataOf(e, "skill_candidate_ready").skill;
    if (s && typeof s.name === "string") out.push(s);
  }
  return out;
}

const LOCALE = "en-US";

export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatRelative(ms: number): string {
  if (!ms) return "";
  const diff = Date.now() - ms;
  const min = Math.round(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} h ago`;
  return new Date(ms).toLocaleDateString(LOCALE, { day: "numeric", month: "short" });
}

export function formatRatio(v: number | null | undefined): string {
  return typeof v === "number" ? `${(v * 100).toFixed(0)}%` : "—";
}

export function formatDuration(ms: number | null | undefined): string {
  if (typeof ms !== "number") return "—";
  return ms < 60000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.floor(ms / 60000)} min ${Math.round((ms % 60000) / 1000)} s`;
}

export function formatNumber(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toLocaleString(LOCALE) : "—";
}

export function formatCost(value: number | string | null | undefined): string {
  if (typeof value === "string") {
    if (!/^\d+(?:\.\d+)?$/.test(value)) return "—";
    const [whole, decimals = ""] = value.split(".");
    return `$${whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${decimals.padEnd(4, "0")}`;
  }
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? value.toLocaleString(LOCALE, {
      style: "currency", currency: "USD", minimumFractionDigits: 4, maximumFractionDigits: 6,
    })
    : "—";
}

export const PHASE_LABEL: Record<string, string> = {
  intake: "Intake",
  plan: "Plan",
  forge: "Forge",
  rule: "Rule",
  validation: "Validation",
  approval: "Approval",
  done: "Done",
};

export const STATUS_LABEL: Record<RunStatus, string> = {
  running: "Running",
  awaiting_approval: "Needs review",
  approved: "Approved",
  rejected: "Rejected",
  failed: "Failed",
};

export const FAIL_LABEL: Record<string, string> = {
  REQUEST_REJECTED: "Request rejected by the gatekeeper",
  PLAN_INVALID: "No valid plan after 3 attempts",
  FORGE_FAILED: "Skill could not be built",
  RULE_FAILED: "Rule did not meet the thresholds",
  VALIDATION_FAILED: "Rule failed on the validation set",
  LLM_ERROR: "LLM API error",
  SANDBOX_ERROR: "Sandbox error",
  INTERNAL_ERROR: "Internal backend error",
};

const TARGET_LABEL: Record<string, string> = { plan: "plan", skill: "skill", recipe: "rule" };

function s(v: unknown): string {
  return typeof v === "string" ? v : "";
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// English one-line title for each step, built from structured event data.
// Unknown event types fall back to the backend `message`.
export function eventTitle(e: RunEvent): string {
  switch (e.type) {
    case "run_started":
      return "Received the request";
    case "plan_ready": {
      const d = dataOf(e, "plan_ready");
      const n = Array.isArray(d.steps) ? d.steps.length : 0;
      return `Drafted a plan with ${plural(n, "step")}`;
    }
    case "skill_reused":
      return `Reusing skill ${s(dataOf(e, "skill_reused").skill?.name)} from the registry`;
    case "capability_missing": {
      const list = dataOf(e, "capability_missing").skills;
      const names = Array.isArray(list) ? list.map((x) => s(x?.name)).filter(Boolean) : [];
      return `Missing ${plural(names.length, "skill")}: ${names.join(", ")}`;
    }
    case "forge_started": {
      const d = dataOf(e, "forge_started");
      return `Building skill ${s(d.skill)} · attempt ${d.attempt} of ${d.max_attempts}`;
    }
    case "skill_tests_failed": {
      const d = dataOf(e, "skill_tests_failed");
      return `${s(d.skill)} failed ${d.tests_failed} of ${d.tests_total} tests`;
    }
    case "skill_candidate_ready": {
      const d = dataOf(e, "skill_candidate_ready");
      return `${s(d.skill?.name)} passed all ${d.tests_total} tests`;
    }
    case "rule_drafted": {
      const d = dataOf(e, "rule_drafted");
      return `Drafted rule ${s(d.recipe?.name)} · attempt ${d.attempt} of ${d.max_attempts}`;
    }
    case "rule_evaluated": {
      const m = dataOf(e, "rule_evaluated").metrics;
      return m?.passed ? "Meets the thresholds on the tuning set" : "Below the thresholds on the tuning set";
    }
    case "validation_done": {
      const m = dataOf(e, "validation_done").metrics;
      return m?.passed ? "Passed on the held-out validation set" : "Failed on the held-out validation set";
    }
    case "summary":
      return "Wrote a summary";
    case "voice_ready":
      return "Voice summary is ready";
    case "awaiting_approval":
      return "Waiting for analyst review";
    case "skill_installed":
      return `Installed ${s(dataOf(e, "skill_installed").skill?.name)} to the registry`;
    case "rule_approved":
      return `Rule ${s(dataOf(e, "rule_approved").rule_name)} approved`;
    case "rule_rejected":
      return "Rule rejected by the analyst";
    case "policy_rejected": {
      const d = dataOf(e, "policy_rejected");
      const target = TARGET_LABEL[s(d.target)] ?? s(d.target);
      return `Gatekeeper blocked the ${target}${d.name ? ` ${s(d.name)}` : ""}`;
    }
    case "run_failed": {
      const code = s(dataOf(e, "run_failed").reason_code);
      return `Run failed: ${FAIL_LABEL[code] ?? code}`;
    }
    default:
      return e.message;
  }
}
