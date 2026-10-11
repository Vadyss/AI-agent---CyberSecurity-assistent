// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useRef, useState, type ReactNode } from "react";
import { api, ApiError } from "@/lib/api";
import { dataOf, FAIL_LABEL, lastOf, runStatus } from "@/lib/derive";
import type { RunState } from "@/lib/engine";
import { IconCheck, IconX } from "./icons";
import { MetricsCard } from "./Metrics";
import { CodeBlock, SectionLabel, Tag } from "./ui";

const MAX_COMMENT = 500;
const MAX_REASON = 500;

// Review card shown inline in the conversation while the run awaits approval.
export function ReviewCard({ run }: { run: RunState }) {
  const [mode, setMode] = useState<"idle" | "reject">("idle");
  const [comment, setComment] = useState("");
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lock = useRef(false); // synchronous guard against double clicks

  const ev = lastOf(run, "awaiting_approval");
  if (!ev || runStatus(run) !== "awaiting_approval") return null;
  const d = dataOf(ev, "awaiting_approval");
  const newSkills = Array.isArray(d.new_skills) ? d.new_skills : [];
  const ruleName = typeof d.recipe?.name === "string" ? d.recipe.name : "rule";

  async function send(kind: "approve" | "reject") {
    if (lock.current) return;
    lock.current = true;
    setPending(true);
    setError(null);
    try {
      if (kind === "approve") await api.approve(run.run_id, comment.trim());
      else await api.reject(run.run_id, reason.trim());
      // Stay locked until the terminal event arrives and this card unmounts.
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't send the decision.");
      lock.current = false;
      setPending(false);
    }
  }

  const reasonOk = reason.trim().length >= 1 && reason.trim().length <= MAX_REASON;

  return (
    <div className="review-card overflow-hidden rounded-lg border border-line">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-base font-medium">Approve detection rule</h2>
          <div className="mt-1 break-all font-mono text-sm text-muted">{ruleName}</div>
        </div>
      </div>

      <div className="space-y-6 p-4">
        <div className="grid gap-6 sm:grid-cols-2">
          <MetricsCard title="Tuning set" hint="Used while drafting" metrics={d.metrics_tuning} />
          <MetricsCard title="Validation set" hint="Checked separately" metrics={d.metrics_validation} />
        </div>

        <div>
          <SectionLabel>Rule JSON</SectionLabel>
          <CodeBlock value={d.recipe} label={`${ruleName}.json`} />
        </div>

        {newSkills.length > 0 ? (
          <div>
            <SectionLabel>New tools to install</SectionLabel>
            <ul className="divide-y divide-line">
              {newSkills.map((s, i) => (
                <li key={`${s?.name}-${i}`} className="py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="break-all font-mono text-sm">{String(s?.name ?? "")}</span>
                    <Tag>{String(s?.kind ?? "")}</Tag>
                  </div>
                  <p className="mt-0.5 text-sm text-muted" style={{ overflowWrap: "anywhere" }}>{String(s?.description ?? "")}</p>
                </li>
              ))}
            </ul>
          </div>
        ) : <p className="text-sm text-muted">No new tools built in this run (reused existing tools)</p>}
      </div>

      <div className="border-t border-line px-4 py-4">
        {mode === "idle" ? (
          <div className="space-y-2.5">
            <input
              aria-label="Approval comment (optional)"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              disabled={pending}
              maxLength={MAX_COMMENT}
              placeholder="Add a comment (optional)"
              className="w-full rounded-md border border-line bg-bg px-3 py-2 text-sm placeholder:text-subtle focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info disabled:opacity-60"
            />
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                disabled={pending}
                onClick={() => setMode("reject")}
                className="secondary-button"
              >
                Reject
              </button>
              <button
                type="button"
                disabled={pending}
                onClick={() => void send("approve")}
                className="primary-button"
              >
                <IconCheck className="size-4" />
                {pending ? "Approving…" : "Approve and install"}
              </button>
            </div>
          </div>
        ) : (
          <div className="space-y-2.5">
            <textarea
              aria-label="Reason for rejecting this rule"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              disabled={pending}
              rows={2}
              maxLength={MAX_REASON}
              autoFocus
              placeholder="Why are you rejecting this rule? (required)"
              className="block w-full resize-none rounded-md border border-line bg-bg px-3 py-2 text-sm placeholder:text-subtle focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info disabled:opacity-60"
            />
            <div className="flex flex-wrap justify-end gap-2">
              <button
                type="button"
                disabled={pending}
                onClick={() => setMode("idle")}
                className="secondary-button"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={pending || !reasonOk}
                onClick={() => void send("reject")}
                className="inline-flex items-center gap-2 rounded-md border border-bad/40 bg-bad/10 px-4 py-2 text-sm font-medium text-bad hover:bg-bad/15 disabled:opacity-50"
              >
                <IconX className="size-4" />
                {pending ? "Rejecting…" : "Reject rule"}
              </button>
            </div>
          </div>
        )}
        {error && (
          <p role="alert" className="mt-2 text-sm text-bad">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

// Final outcome line shown at the end of a finished run.
export function Outcome({ run }: { run: RunState }) {
  const approved = lastOf(run, "rule_approved");
  const rejected = lastOf(run, "rule_rejected");
  const failed = lastOf(run, "run_failed");

  if (approved) {
    const d = dataOf(approved, "rule_approved");
    const installed = run.events
      .filter((e) => e.type === "skill_installed")
      .map((e) => String(dataOf(e, "skill_installed").skill?.name ?? ""));
    return (
      <Callout tone="ok" title={`Rule ${String(d.rule_name ?? "")} approved`}>
        {installed.length > 0 && <p>Installed tools: {installed.join(", ")}</p>}
        {typeof d.comment === "string" && d.comment && <p>Comment: {d.comment}</p>}
      </Callout>
    );
  }
  if (rejected) {
    const d = dataOf(rejected, "rule_rejected");
    return (
      <Callout tone="neutral" title="Rule rejected">
        <p>{String(d.reason ?? "")}</p>
      </Callout>
    );
  }
  if (failed) {
    const d = dataOf(failed, "run_failed");
    const code = String(d.reason_code ?? "");
    return (
      <Callout tone="bad" title={FAIL_LABEL[code] ?? "Run failed"}>
        <p>{String(d.reason ?? "")}</p>
        <p className="font-mono text-xs text-subtle">{code}</p>
      </Callout>
    );
  }
  return null;
}

function Callout({
  tone,
  title,
  children,
}: {
  tone: "ok" | "bad" | "neutral";
  title: string;
  children?: ReactNode;
}) {
  const icon =
    tone === "ok" ? (
      <IconCheck className="size-4 text-ok" />
    ) : tone === "bad" ? (
      <IconX className="size-4 text-bad" />
    ) : (
      <IconX className="size-4 text-subtle" />
    );
  return (
    <div className="flex gap-3 py-4">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <div className="min-w-0 space-y-1 text-sm">
        <div className="font-medium">{title}</div>
        <div className="space-y-1 text-muted">{children}</div>
      </div>
    </div>
  );
}
