// Copyright (c) 2026 Adam Krúpa and Ondra Csajka. MIT License.
import { dataOf, formatCost, formatDuration, formatNumber, lastOf, runRequest, runStatus } from "@/lib/derive";
import type { RunState } from "@/lib/engine";
import type { RunStats } from "@/lib/types";
import { usageCost } from "./Usage";

type Row = { key: keyof RunStats; label: string; fmt: (v: number | string | null | undefined) => string; lowerIsBetter: boolean };

const ROWS: Row[] = [
  { key: "duration_ms", label: "Duration", fmt: (v) => formatDuration(v as number | null | undefined), lowerIsBetter: true },
  { key: "llm_calls", label: "LLM calls", fmt: (v) => formatNumber(v as number | null | undefined), lowerIsBetter: true },
  { key: "tokens_total", label: "Tokens", fmt: (v) => formatNumber(v as number | null | undefined), lowerIsBetter: true },
  { key: "cost_usd", label: "Cost (USD)", fmt: formatCost, lowerIsBetter: true },
  { key: "skills_built", label: "Skills built", fmt: (v) => formatNumber(v as number | null | undefined), lowerIsBetter: true },
  { key: "skills_reused", label: "Skills reused", fmt: (v) => formatNumber(v as number | null | undefined), lowerIsBetter: false },
];

function statsOf(run: RunState): Partial<RunStats> | null {
  const e = lastOf(run, "summary");
  const s = e ? dataOf(e, "summary").stats : undefined;
  return s && typeof s === "object" ? s : null;
}

// Contract 12.5: summary.stats of the last two finished runs.
export function Compare({ runs }: { runs: RunState[] }) {
  const done = runs
    .filter((r) => {
      const st = runStatus(r);
      return (st === "approved" || st === "rejected" || st === "failed") && statsOf(r);
    })
    .slice(0, 2)
    .reverse(); // older on the left

  if (done.length < 2) {
    return <p className="text-sm text-subtle">Finish two runs to compare them side by side.</p>;
  }
  const [a, b] = done;
  const sa = statsOf(a)!;
  const sb = statsOf(b)!;
  const usageA = lastOf(a, "llm_usage");
  const usageB = lastOf(b, "llm_usage");
  const costA = usageA ? dataOf(usageA, "llm_usage").totals : undefined;
  const costB = usageB ? dataOf(usageB, "llm_usage").totals : undefined;

  return (
    <div>
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-subtle">
            <th className="pb-2 font-normal" />
            <th className="max-w-0 truncate pb-2 text-right font-normal" title={runRequest(a) ?? ""}>
              <span className="block">Previous</span>
              <span className="mt-1 block truncate text-xs" title={runRequest(a) ?? ""}>{runRequest(a) ?? a.run_id}</span>
            </th>
            <th className="max-w-0 truncate pb-2 text-right font-normal" title={runRequest(b) ?? ""}>
              <span className="block">Latest</span>
              <span className="mt-1 block truncate text-xs" title={runRequest(b) ?? ""}>{runRequest(b) ?? b.run_id}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map((r) => {
            const va = sa[r.key];
            const vb = sb[r.key];
            let tone = "";
            if (!(r.key === "cost_usd" && (costA || costB)) && typeof va === "number" && typeof vb === "number" && va !== vb) {
              tone = (r.lowerIsBetter ? vb < va : vb > va) ? "text-ok" : "text-bad";
            }
            return (
              <tr key={r.key} className="border-t border-line">
                <td className="py-2 text-muted">{r.label}</td>
                <td className="py-2 text-right tabular-nums text-muted">{r.key === "cost_usd" && costA ? usageCost(costA.cost_usd) : r.fmt(va)}</td>
                <td className={`py-2 text-right font-medium tabular-nums ${tone}`}>{r.key === "cost_usd" && costB ? usageCost(costB.cost_usd) : r.fmt(vb)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
