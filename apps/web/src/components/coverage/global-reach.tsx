"use client";

import { useState } from "react";

import { StatePlaceholder } from "@/components/data-state";
import { Input } from "@/components/ui/input";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { filterMeasured } from "@/lib/coverage/logic";
import type { MeasuredCoverageRow } from "@/lib/coverage/types";
import { formatDate } from "@/lib/format";

/**
 * Measured reach by jurisdiction and metric (GET /verification/coverage): observations, distinct
 * entities, unknown values, sources, period span and newest known-at. The API has no exchange or
 * asset-class dimension, which is stated here. A jurisdiction that is absent has no stored
 * observation: that is "no coverage measured", not a share of the world.
 */
export function GlobalReach({ rows }: { rows: readonly MeasuredCoverageRow[] }) {
  const [q, setQ] = useState("");
  const shown = filterMeasured(rows, q);
  return (
    <Blueprint as="section" aria-labelledby="reach-heading" id="reach" className="flex scroll-mt-24 flex-col gap-3 px-5 py-5">
      <PanelHeading kicker="Global reach" title={<span id="reach-heading">Measured coverage by jurisdiction and metric</span>} />
      <p className="max-w-prose text-sm text-text">
        {rows.length === 0
          ? "No observation rows are stored yet."
          : `${new Set(rows.map((r) => r.jurisdiction)).size} jurisdictions have stored observations across ${rows.length} jurisdiction-metric rows. Absent jurisdictions are unmeasured, and no row is extrapolated to a global percentage.`}
      </p>
      <p className="text-xs text-text-muted">
        Dimensions the API provides: jurisdiction, domain, metric. No endpoint returns coverage by exchange or by asset class, nor a tested-symbol count, so those cuts are not shown.
      </p>
      {rows.length === 0 ? (
        <StatePlaceholder state="not_covered" reason="Nothing has been measured yet; run the source collectors and the shadow collection." />
      ) : (
        <>
          <label className="flex max-w-sm min-w-0 flex-col gap-1 text-xs text-text-muted">
            Filter jurisdiction, domain or metric
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. DE or inflation" />
          </label>
          <div className="relative max-h-[26rem] overflow-auto border border-border-soft">
            <table className="w-full min-w-[46rem] text-xs">
              <caption className="sr-only">Measured coverage by jurisdiction and metric</caption>
              <thead className="sticky top-0 bg-surface-muted">
                <tr className="border-b border-border text-left text-[11px] tracking-[0.08em] text-text-muted uppercase">
                  {["Jurisdiction", "Domain", "Metric", "Observations", "Entities", "Unknown values", "Sources", "Period span", "Newest known"].map((c) => (
                    <th key={c} scope="col" className="px-2 py-1.5 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={`${r.jurisdiction}|${r.domain}|${r.metric}`} className="border-b border-border-soft last:border-0">
                    <th scope="row" className="num px-2 py-1.5 text-left font-medium">
                      {r.jurisdiction}
                    </th>
                    <td className="px-2 py-1.5">{r.domain}</td>
                    <td className="num px-2 py-1.5 break-all">{r.metric}</td>
                    <td className="num px-2 py-1.5">{r.observations}</td>
                    <td className="num px-2 py-1.5">{r.entities === null ? "Not applicable" : r.entities}</td>
                    <td className="num px-2 py-1.5">{r.unknownValues}</td>
                    <td className="num px-2 py-1.5">{r.sources}</td>
                    <td className="num px-2 py-1.5">
                      {r.earliestPeriod && r.latestPeriod ? `${formatDate(r.earliestPeriod.slice(0, 10))} to ${formatDate(r.latestPeriod.slice(0, 10))}` : "Unknown"}
                    </td>
                    <td className="num px-2 py-1.5">{r.newestKnownAt ? formatDate(r.newestKnownAt.slice(0, 10)) : "Unknown"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {shown.length === 0 ? <p className="text-xs text-text-muted">No row matches the filter.</p> : null}
        </>
      )}
    </Blueprint>
  );
}

