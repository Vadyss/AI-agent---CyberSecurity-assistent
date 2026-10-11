// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
// Data types per docs/kontrakt.md (chapters 6, 9, 10).
// Fields marked [untrusted] are always rendered as plain text.

export const CONTRACT_VERSION = 1;

export type SkillKind = "parser" | "aggregation" | "enrichment";
export type SkillOrigin = "seed" | "agent";
export type SkillStatus = "candidate" | "installed";

export interface SkillInfo {
  name: string;
  version: number;
  kind: SkillKind | string;
  description: string; // [untrusted] when origin=agent
  origin: SkillOrigin | string;
  status: SkillStatus | string;
  created_by_run: string | null;
  created_at: string;
}

export interface Metrics {
  true_positives: number;
  false_positives: number;
  false_negatives: number;
  precision: number | null;
  recall: number | null;
  thresholds: { min_precision: number; min_recall: number };
  passed: boolean;
}

// The frontend does not parse recipes: only `name` and the full JSON. [untrusted]
export type Recipe = { name: string } & Record<string, unknown>;

export type RunStatus =
  | "running"
  | "awaiting_approval"
  | "approved"
  | "rejected"
  | "failed";

export interface RunInfo {
  run_id: string;
  request: string; // [untrusted]
  status: RunStatus | string;
  created_at: string;
  finished_at: string | null;
  last_seq: number;
}

export interface RunStats {
  duration_ms: number;
  llm_calls: number;
  tokens_total: number | null;
  cost_usd: string | number | null; // exact decimal string; null when any call cost is unknown
  skills_built: number;
  skills_reused: number;
}

export type Phase =
  | "intake"
  | "plan"
  | "forge"
  | "rule"
  | "validation"
  | "approval"
  | "done";

export const PHASES: Phase[] = [
  "intake",
  "plan",
  "forge",
  "rule",
  "validation",
  "approval",
  "done",
];

export interface RunEvent {
  type: string;
  run_id: string;
  seq: number;
  timestamp: string;
  phase: Phase | string;
  message: string; // [untrusted]
  data: Record<string, unknown>;
}

// `data` payload of each event type (chapter 10).
export interface EventDataMap {
  llm_usage: UsageData;
  run_started: { request: string };
  plan_ready: { steps: string[]; skills_needed: string[] };
  skill_reused: { skill: SkillInfo };
  capability_missing: { skills: { name: string; description: string }[] };
  forge_started: { skill: string; attempt: number; max_attempts: number };
  skill_tests_failed: {
    skill: string;
    attempt: number;
    tests_total: number;
    tests_failed: number;
    error_excerpt: string;
  };
  skill_candidate_ready: {
    skill: SkillInfo;
    attempt: number;
    tests_total: number;
    code_sha256: string;
  };
  rule_drafted: {
    attempt: number;
    max_attempts: number;
    recipe: Recipe;
    explanation: string;
  };
  rule_evaluated: { attempt: number; dataset: string; metrics: Metrics };
  validation_done: { dataset: string; metrics: Metrics };
  summary: { text: string; stats: RunStats };
  voice_ready: { audio_url: string };
  awaiting_approval: {
    recipe: Recipe;
    metrics_tuning: Metrics;
    metrics_validation: Metrics;
    new_skills: SkillInfo[];
  };
  skill_installed: { skill: SkillInfo };
  rule_approved: { rule_name: string; comment: string | null };
  rule_rejected: { reason: string };
  policy_rejected: {
    target: "plan" | "skill" | "recipe" | string;
    name: string | null;
    attempt: number;
    violations: { code: string; detail: string }[];
  };
  run_failed: { reason_code: string; reason: string };
}

export interface UsageRecord {
  run_id: string; call_id: number; step: string; iteration: number; attempt: number;
  model: string; input_tokens: number | null; cached_tokens: number | null;
  output_tokens: number | null; total_tokens: number | null; cache_write_tokens: number | null;
  cost_usd: string | null; cost_source: string; currency: "USD";
  duration_ms: number; timestamp: string; estimated: boolean; retry: boolean;
  status: string; warning: string | null;
}
export interface UsageStep {
  step: string; calls: number; retries: number; cost_usd: string | null; share_percent: string | null;
}
export interface UsageTotals {
  calls: number; total_tokens: number | null; known_tokens: number; cost_usd: string | null;
  known_cost_usd: string; unknown_cost_calls: number; estimated_calls: number;
  calculated_cost_calls: number; retries: number; steps: UsageStep[];
}
export interface UsageSummary {
  totals: UsageTotals; most_expensive_step: string | null; average_cost_usd: string | null;
  average_run_count: number; observations: string[];
}
export interface UsageData {
  kind: "call" | "summary"; record: UsageRecord | null; totals: UsageTotals; summary: UsageSummary | null;
}

export type KnownEventType = keyof EventDataMap;

export const TERMINAL_EVENTS = ["rule_approved", "rule_rejected", "run_failed"];

export interface ApiErrorBody {
  error: { code: string; message: string };
}
