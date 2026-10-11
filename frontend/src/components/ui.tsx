// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { STATUS_LABEL } from "@/lib/derive";
import type { RunStatus } from "@/lib/types";
import { IconCheck, IconCopy } from "./icons";

const STATUS_DOT: Record<RunStatus, string> = {
  running: "bg-info",
  awaiting_approval: "bg-warn",
  approved: "bg-ok",
  rejected: "bg-subtle",
  failed: "bg-bad",
};

export function StatusDot({ status }: { status: RunStatus | null }) {
  return <span className={`inline-block size-1.5 shrink-0 rounded-full ${status ? STATUS_DOT[status] : "bg-subtle"}`} />;
}

export function StatusPill({ status }: { status: RunStatus | null }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-muted">
      <StatusDot status={status} />
      {status ? STATUS_LABEL[status] : "Starting"}
    </span>
  );
}

export function Tag({ children, mono }: { children: ReactNode; mono?: boolean }) {
  return (
    <span
      className={`inline-flex items-center text-xs leading-5 text-muted ${
        mono ? "font-mono" : ""
      }`}
    >
      {children}
    </span>
  );
}

// Pretty-printed JSON rendered as plain text (contract 5.2).
export function CodeBlock({ value, label = "json" }: { value: unknown; label?: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  const codeRef = useRef<HTMLPreElement>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  let text: string;
  try {
    text = JSON.stringify(value, null, 2) ?? "null";
  } catch {
    text = String(value);
  }
  async function copy() {
    setError("");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Clipboard unavailable. The code is selected; use your copy shortcut.");
      if (codeRef.current) {
        codeRef.current.focus();
        const range = document.createRange();
        range.selectNodeContents(codeRef.current);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(range);
      }
    }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${label.replace(/\.json$/i, "").replace(/[^a-z0-9_-]/gi, "_").slice(0, 80) || "rule"}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <details className="code-block overflow-hidden rounded-md border border-line">
      <summary className="code-summary bg-surface px-3 py-3 text-sm text-muted" aria-label={`View ${label}`}>
        <span className="font-mono break-all">{label}</span>
      </summary>
      <div className="code-tools flex justify-end gap-3 bg-surface px-3 pb-3 text-xs text-muted"><button type="button" onClick={copy} className="inline-flex items-center gap-1 rounded px-1 hover:text-fg">
          {copied ? <IconCheck className="size-3.5" /> : <IconCopy className="size-3.5" />}
          {copied ? "Copied" : "Copy"}
        </button>
        <button type="button" onClick={download} className="rounded px-1 hover:text-fg">Download JSON</button>
      </div>
      <pre ref={codeRef} tabIndex={0} className="max-h-80 overflow-auto bg-bg p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
        {text}
      </pre>
      {error && <p role="status" className="copy-feedback px-3 pb-3">{error}</p>}
    </details>
  );
}

export function SectionLabel({ children }: { children: ReactNode }) {
  return <div className="mb-2 text-sm font-medium text-muted">{children}</div>;
}
