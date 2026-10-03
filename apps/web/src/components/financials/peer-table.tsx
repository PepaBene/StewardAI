"use client";

import { useState } from "react";

import { ChartFrame, Segmented } from "@/components/data-state";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { PEER_METRIC_META, peerComparison, peerState, type PeerComparison } from "@/lib/financials/logic";
import type { PeerMetricsPayload } from "@/lib/financials/types";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * Bar extents on one shared scale around a zero baseline: a negative value extends left of zero
 * (and is drawn in the loss colour), a positive one right, so -20% and +20% are never the same bar.
 */
export function barGeometry(values: readonly (number | null)[]): { bars: ({ left: number; width: number } | null)[]; zero: number } {
  const finite = values.filter((v): v is number => v !== null && Number.isFinite(v));
  const lo = Math.min(0, ...finite);
  const hi = Math.max(0, ...finite);
  const span = hi - lo || 1e-9;
  const pos = (v: number) => ((v - lo) / span) * 100;
  const zero = pos(0);
  const out = values.map((v) => (v === null || !Number.isFinite(v) ? null : { left: Math.min(pos(v), zero), width: Math.abs(pos(v) - zero) }));
  return { bars: out, zero };
}

function fmtValue(v: number | null, unit: PeerComparison["unit"]): string {
  if (v === null) return "n/m";
  return unit === "percent" ? formatPct(v, 1) : `${formatNumber(v, 2)}×`;
}

/**
 * The holding against the mean of its comparable CONFIRMED peers, per metric, on the one
 * formula the backend documents. The API returns the peer mean, not each peer's value, so no
 * per-peer ranking is drawn. Covered/total peers are always shown, and every confirmed peer
 * that is out lists why (different basis, period mismatch, non-meaningful value, no facts).
 */
export function PeerTable({ data, ticker }: { data: PeerMetricsPayload; ticker: string }) {
  const ids = data.metrics.map((m) => m.id);
  const [choice, setChoice] = useState<string | null>(null);
  const metricId = choice && ids.includes(choice) ? choice : ids[0];
  const c = metricId ? peerComparison(data, metricId) : null;
  const status = peerState(data, c);
  const all = ids.map((id) => peerComparison(data, id)).filter((x): x is PeerComparison => x !== null);
  const bars = c
    ? [
        { key: "holding", label: ticker, value: c.holdingValue, subject: true },
        { key: "mean", label: `Peer mean (${c.covered} of ${c.total})`, value: c.mean, subject: false },
      ]
    : [];
  const geometry = barGeometry(bars.map((b) => b.value));

  return (
    <ChartFrame
      id="peers"
      kicker="Peer comparison"
      title={c ? `${c.label}: ${ticker} against its confirmed peers` : "Peer comparison"}
      takeaway={
        c && c.holdingValue !== null && c.mean !== null
          ? `${ticker} is at ${fmtValue(c.holdingValue, c.unit)}; the mean of ${c.covered} comparable confirmed peers is ${fmtValue(c.mean, c.unit)}.`
          : c
            ? `No comparison for this metric: ${c.holdingValue === null ? (c.holdingReason ?? "the holding has no value") : "no confirmed peer is comparable"}.`
            : "No confirmed peer, so no comparison exists."
      }
      units={c ? `${c.covered} of ${c.total} confirmed peers comparable · ${c.excluded.length} excluded below${c.periodEnd ? ` · period ending ${formatDate(c.periodEnd)}` : ""}${c.basis ? ` · ${c.basis.replaceAll("_", " ")}` : ""}` : "0 confirmed peers"}
      hasValue={!!c && data.confirmedPeers.length > 0}
      controls={ids.length > 1 ? <Segmented label="Metric" value={metricId} onChange={setChoice} options={ids.map((id) => ({ id, label: PEER_METRIC_META[id]?.label ?? id }))} /> : null}
      provenance={{
        source: "Filed statement facts of the holding and its confirmed peers (SEC / ESEF), one formula per metric",
        asOf: c?.periodEnd ?? null,
        knownAt: data.knownAsOf,
        method: c?.definition ?? data.universe ?? undefined,
        state: status.state,
        reason: status.reason,
        coverage: c ? { covered: c.covered, total: c.total, unit: "confirmed peers" } : null,
      }}
      table={{
        caption: "Peer metrics: the holding and the mean of comparable confirmed peers",
        columns: ["Metric", ticker, "Peer mean", "Peers covered", "Period end"],
        rows: all.map((x) => [x.label, fmtValue(x.holdingValue, x.unit), fmtValue(x.mean, x.unit), `${x.covered} of ${x.total}`, x.periodEnd ?? "—"]),
      }}
    >
      {c ? (
        <>
          <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
            {bars.map((b, i) => (

              <li key={b.key} className={cn("grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_4.5rem] items-center gap-2 text-sm", b.subject && "font-semibold")}>
                <span className="truncate">{b.label}</span>
                <span className="relative flex h-2.5 bg-surface-muted" aria-hidden="true">
                  {geometry.bars[i] ? (
                    <span className={cn("absolute top-0 block h-full", b.value !== null && b.value < 0 ? "bg-loss" : b.subject ? "bg-accent-ramp-1" : "bg-accent-300")} style={{ left: `${geometry.bars[i]!.left}%`, width: `${geometry.bars[i]!.width}%` }} />
                  ) : null}
                  <span className="absolute top-0 block h-full w-px bg-text-muted" style={{ left: `${geometry.zero}%` }} />
                </span>
                <span className="num text-right">{fmtValue(b.value, c.unit)}</span>
              </li>
            ))}
          </ol>
          {c.holdingValue === null && c.holdingReason ? <p className="text-xs text-text-muted">{ticker}: {c.holdingReason}</p> : null}
          {c.included.length > 0 ? <p className="text-xs text-text-muted">Included: {c.included.join(", ")}</p> : null}
          {c.excluded.length > 0 ? (
            <section aria-labelledby="peer-excluded-heading" className="flex min-w-0 flex-col gap-1">
              <h3 id="peer-excluded-heading" className="font-heading text-lg font-semibold">
                Excluded peers
              </h3>
              <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
                {c.excluded.map((e) => (
                  <li key={e.ticker} className="border-b border-border-soft pb-1">
                    <span className="font-mono text-xs font-medium">{e.ticker}</span>: {e.reason}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </>
      ) : null}
      {data.holdingPeriod && data.holdingPeriod.unreconciledInputs.length > 0 ? (
        <p className="text-xs text-text-muted">Inputs of the holding not covered by a reconciliation check: {data.holdingPeriod.unreconciledInputs.join(", ")}.</p>
      ) : null}
    </ChartFrame>
  );
}
