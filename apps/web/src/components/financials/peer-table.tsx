"use client";

import { useState } from "react";

import { ChartFrame, Segmented } from "@/components/data-state";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { peerComparison } from "@/lib/financials/logic";
import type { PeerSet } from "@/lib/financials/types";
import { cn } from "@/lib/utils";

/**
 * Peer comparison on one documented definition per metric, ranked, with the subject
 * highlighted, the covered/excluded count and every exclusion's reason. A peer on another
 * accounting basis appears only when its figure uses the same definition.
 */
export function PeerTable({ set }: { set: PeerSet }) {
  const ids = Object.keys(set.definitions);
  const [metric, setMetric] = useState(ids[0]);
  const c = peerComparison(set, metric);
  const def = set.definitions[metric];
  const fmt = (v: number) => (def.unit === "percent" ? formatPct(v, 1) : `${formatNumber(v, 2)}×`);
  const max = Math.max(...c.rows.map((r) => Math.abs(r.value)), 1e-9);
  const subject = c.rows.find((r) => r.subject);
  const rank = subject ? c.rows.findIndex((r) => r.subject) + 1 : null;

  return (
    <ChartFrame
      id="peers"
      kicker="Peer comparison"
      title={`${def.label}: ${set.subject.name} against its peers`}
      takeaway={
        subject && c.peerMedian !== null
          ? `${set.subject.name} ranks ${rank} of ${c.rows.length} at ${fmt(subject.value)}; the median of ${c.covered} comparable peers is ${fmt(c.peerMedian)}.`
          : "Not enough comparable peers to rank this metric."
      }
      units={`${c.covered} of ${c.total} peers comparable · ${c.excluded.length} excluded below · definition: ${c.definition}`}
      controls={<Segmented label="Metric" options={ids.map((id) => ({ id, label: set.definitions[id].label }))} value={metric} onChange={setMetric} />}
      provenance={{
        source: "Filed statements of each issuer (SEC / ESEF), one formula per metric",
        asOf: null,
        method: c.definition,
        state: c.excluded.length > 0 ? "partial" : "verified",
        reason: c.excluded.length > 0 ? `${c.excluded.length} peer${c.excluded.length === 1 ? "" : "s"} excluded; see reasons below.` : null,
        coverage: { covered: c.covered, total: c.total, unit: "peers" },
      }}
      table={{
        caption: `${def.label}, ranked`,
        columns: ["Company", "Basis", def.label],
        rows: c.rows.map((r) => [`${r.name}${r.subject ? " (subject)" : ""}`, r.basis, fmt(r.value)]),
      }}
    >
      <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
        {c.rows.map((r, i) => (
          <li key={r.ticker} data-subject={r.subject || undefined} className={cn("grid grid-cols-[1.5rem_11rem_1fr_4.5rem] items-center gap-2 text-sm", r.subject && "font-semibold")}>
            <span className="num text-text-faint">{i + 1}</span>
            <span className="truncate">
              {r.name}
              <span className="ml-1.5 font-mono text-[10px] font-normal text-text-muted">{r.basis}</span>
            </span>
            <span className="flex h-2.5 bg-surface-muted" aria-hidden="true">
              <span className={cn("block h-full", r.subject ? "bg-accent-ramp-1" : "bg-accent-300")} style={{ width: `${(Math.abs(r.value) / max) * 100}%` }} />
            </span>
            <span className="num text-right">{fmt(r.value)}</span>
          </li>
        ))}
      </ol>
      {c.peerMedian !== null ? <p className="num text-xs text-text-muted">Peer median {fmt(c.peerMedian)} (subject excluded)</p> : null}
      {c.excluded.length > 0 ? (
        <section aria-labelledby="peer-excluded-heading" className="flex flex-col gap-1">
          <h3 id="peer-excluded-heading" className="font-heading text-lg font-semibold">
            Excluded peers
          </h3>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
            {c.excluded.map((e) => (
              <li key={e.ticker} className="border-b border-border-soft pb-1">
                <span className="font-medium">{e.name}</span> <span className="font-mono text-xs text-text-muted">{e.ticker}</span>: {e.reason}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </ChartFrame>
  );
}
