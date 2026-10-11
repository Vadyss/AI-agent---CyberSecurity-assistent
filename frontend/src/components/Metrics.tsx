// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import { formatRatio } from "@/lib/derive";
import type { Metrics } from "@/lib/types";

// Pass/fail is decided only by `passed` (contract rule 0.3).
function Verdict({ passed }: { passed: boolean | undefined }) {
  if (passed === undefined) return null;
  return (
    <span className={`text-xs font-medium ${passed ? "text-ok" : "text-bad"}`}>
      {passed ? "Passed" : "Below threshold"}
    </span>
  );
}

function Ratio({ label, value, min }: { label: string; value: number | null | undefined; min?: number }) {
  const pct = typeof value === "number" ? Math.max(0, Math.min(1, value)) * 100 : 0;
  const below = typeof value === "number" && typeof min === "number" && value < min;
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className="tabular-nums">
          <span className={below ? "text-bad" : "text-fg"}>{formatRatio(value)}</span>
          {typeof min === "number" && <span className="text-subtle"> / min {formatRatio(min)}</span>}
        </span>
      </div>
      <div className="relative mt-1.5 h-1 rounded-full bg-surface">
        <div className={`h-full rounded-full ${below ? "bg-bad" : "bg-fg"}`} style={{ width: `${pct}%` }} />
        {typeof min === "number" && (
          <div
            className="absolute -top-1 h-3 w-px bg-subtle"
            style={{ left: `${Math.max(0, Math.min(1, min)) * 100}%` }}
          />
        )}
      </div>
    </div>
  );
}

export function MetricsCard({
  title,
  hint,
  metrics,
}: {
  title: string;
  hint?: string;
  metrics: Partial<Metrics> | undefined;
}) {
  if (!metrics) return null;
  const th = metrics.thresholds;
  return (
    <div className="metrics-card min-w-0">
      <div className="mb-3 flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium">{title}</div>
          {hint && <div className="text-xs text-subtle">{hint}</div>}
        </div>
        <Verdict passed={metrics.passed} />
      </div>
      <div className="space-y-2.5">
        <Ratio label="Precision" value={metrics.precision} min={th?.min_precision} />
        <Ratio label="Recall" value={metrics.recall} min={th?.min_recall} />
      </div>
      <dl className="mt-3 grid grid-cols-3 border-t border-line pt-2.5 text-xs">
        <Count label="True positives" value={metrics.true_positives} />
        <Count label="False positives" value={metrics.false_positives} />
        <Count label="False negatives" value={metrics.false_negatives} />
      </dl>
    </div>
  );
}

function Count({ label, value }: { label: string; value?: number }) {
  return (
    <div>
      <dt className="text-subtle">{label}</dt>
      <dd className="mt-0.5 font-medium tabular-nums">{typeof value === "number" ? value : "—"}</dd>
    </div>
  );
}

export function MetricsInline({ metrics }: { metrics: Partial<Metrics> | undefined }) {
  if (!metrics) return null;
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted tabular-nums">
      <span>Precision {formatRatio(metrics.precision)}</span>
      <span>Recall {formatRatio(metrics.recall)}</span>
      <span>
        TP {metrics.true_positives ?? "—"} · FP {metrics.false_positives ?? "—"} · FN {metrics.false_negatives ?? "—"}
      </span>
    </div>
  );
}
