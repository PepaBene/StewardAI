"use client";

import { LoaderCircleIcon, XIcon } from "lucide-react";
import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { BlueprintCorners } from "@/components/ui/blueprint";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatMetric, type Baseline, type MetricInfo, type ResultRow } from "@/lib/discover/discover";
import { cn } from "@/lib/utils";

import { SELECT } from "./filter-builder";

type Scope = "global" | "home_market";
type Load = { kind: "idle" } | { kind: "loading" } | { kind: "error"; message: string } | { kind: "ok"; items: Baseline[] };

/**
 * Side-by-side comparison of the ticked rows. A baseline column (median of covered securities,
 * with count and scope) can be added for stocks; below five comparable peers it says so instead
 * of showing a median.
 */
export function CompareTray({
  rows,
  catalogue,
  onRemove,
  onClear,
  showBaseline,
}: {
  rows: readonly ResultRow[];
  catalogue: readonly MetricInfo[];
  onRemove: (key: string) => void;
  onClear: () => void;
  showBaseline: boolean;
}) {
  const [scope, setScope] = useState<Scope>("global");
  const [base, setBase] = useState<Load>({ kind: "idle" });
  if (rows.length === 0) return null;

  const numeric = catalogue.filter((m) => m.kind === "numeric" && rows.some((r) => r.values[m.id]?.value !== null && r.values[m.id]?.value !== undefined));
  const home = rows[0]?.identity?.country ?? null;

  const loadBaseline = async () => {
    setBase({ kind: "loading" });
    const ids = numeric.map((m) => m.id).filter((id) => id !== "qvm_score" && id !== "data_coverage").slice(0, 12);
    try {
      const r = await getBrowserApiClient().GET("/discover/baselines", {
        params: { query: { scope, country: scope === "home_market" ? (home ?? undefined) : undefined, metrics: ids.join(",") } },
      });
      if (r.response.ok && r.data) setBase({ kind: "ok", items: r.data });
      else setBase({ kind: "error", message: `The baseline failed (HTTP ${r.response.status}).` });
    } catch (err) {
      setBase({ kind: "error", message: err instanceof Error ? err.message : String(err) });
    }
  };
  const baselineFor = (id: string) => (base.kind === "ok" ? base.items.find((b) => b.metric === id) : undefined);

  return (
    <section aria-labelledby="compare-heading" className="blueprint space-y-3 px-5 py-5">
      <BlueprintCorners />
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="compare-heading" className="text-[22px] leading-tight font-semibold">
          Comparing {rows.length}
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          {showBaseline ? (
            <>
              <label className="flex items-center gap-1.5 text-xs text-text-muted">
                Baseline of
                <select value={scope} onChange={(e) => { setScope(e.target.value as Scope); setBase({ kind: "idle" }); }} className={SELECT}>
                  <option value="global">all covered issuers</option>
                  <option value="home_market" disabled={!home}>
                    {home ? `${home} issuers (first row's market)` : "home market (country unknown)"}
                  </option>
                </select>
              </label>
              <Button type="button" variant="outline" size="sm" onClick={() => void loadBaseline()} disabled={base.kind === "loading"}>
                {base.kind === "loading" ? <LoaderCircleIcon aria-hidden="true" className="animate-spin" /> : null}
                Add baseline
              </Button>
            </>
          ) : null}
          <Button type="button" variant="ghost" size="sm" onClick={onClear}>
            Clear
          </Button>
        </div>
      </div>
      {base.kind === "error" ? <AlertMessage title="Baseline unavailable">{base.message}</AlertMessage> : null}
      <div className="overflow-x-auto border border-border">
        <table className="w-full min-w-[40rem] text-sm">
          <caption className="sr-only">Side-by-side comparison of the selected rows; unknown values are marked, never zero.</caption>
          <thead className="bg-surface-muted">
            <tr className="border-b border-border text-xs text-text-muted">
              <th scope="col" className="px-3 py-2 text-left font-medium">Metric</th>
              {rows.map((r) => (
                <th key={r.key} scope="col" className="px-3 py-2 text-right font-medium">
                  {r.name ?? r.key}
                  <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${r.name ?? r.key} from comparison`} onClick={() => onRemove(r.key)}>
                    <XIcon aria-hidden="true" />
                  </Button>
                </th>
              ))}
              {base.kind === "ok" ? <th scope="col" className="px-3 py-2 text-right font-medium">Baseline median</th> : null}
            </tr>
          </thead>
          <tbody>
            {numeric.map((m) => {
              const b = baselineFor(m.id);
              return (
                <tr key={m.id} className="border-b border-border last:border-0">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">{m.label}</th>
                  {rows.map((r) => {
                    const text = formatMetric(r.values[m.id], m.unit);
                    return (
                      <td key={r.key} className={cn("num px-3 py-1.5 text-right", text === null && "text-text-muted")}>
                        {text ?? "Unknown"}
                      </td>
                    );
                  })}
                  {base.kind === "ok" ? (
                    <td className="num px-3 py-1.5 text-right text-xs">
                      {b ? (
                        b.median !== null && b.median !== undefined ? (
                          <>
                            {formatMetric({ value: b.median }, m.unit)}
                            <span className="block text-text-muted">
                              {b.comparable} of {b.members} · {b.scope_detail}
                            </span>
                          </>
                        ) : (
                          <span className="text-text-muted">{b.median_withheld_reason ?? "no median"}</span>
                        )
                      ) : (
                        <span className="text-text-muted">not requested</span>
                      )}
                    </td>
                  ) : null}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {base.kind === "ok" ? (
        <details className="text-xs text-text-muted">
          <summary className="cursor-pointer text-accent-ink hover:underline">Peers left out of the baselines</summary>
          <ul className="mt-1 space-y-0.5">
            {base.items.map((b) => (
              <li key={b.metric}>
                {b.label}: {b.excluded.length} excluded
                {b.excluded.length > 0 ? ` (${[...new Set(b.excluded.map((e) => e.reason.replace(/: .*|\s\d{4}-.*/, "")))].slice(0, 3).join("; ")})` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
