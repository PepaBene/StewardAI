"use client";

import { useState } from "react";

import { SVG_BASE, AXIS_TEXT } from "@/components/charts/shared";
import { ChartFrame, Segmented } from "@/components/data-state";
import { formatNumber } from "@/lib/charts/format";
import { extent, linearScale, niceTicks } from "@/lib/charts/scale";
import { linePath } from "@/lib/charts/path";
import { eventStudyGate, eventWindows, meanWindow, barsForBasis } from "@/lib/markets/logic";
import { ACTION_LABELS, type ListingSeries } from "@/lib/markets/types";

const W = 640;
const H = 240;

const WINDOWS = [
  { id: "5", label: "±5 sessions" },
  { id: "10", label: "±10 sessions" },
] as const;

/**
 * Indexed price path around each recorded action (100 = last close before the event), faint
 * per event with their mean in the accent. The panel exists only when the adjusted series is
 * reconciled: otherwise a split would read as a −75% reaction, so the gate's reason replaces it.
 */
export function EventStudyPanel({ listing }: { listing: ListingSeries }) {
  const [span, setSpan] = useState<(typeof WINDOWS)[number]["id"]>("5");
  const gate = eventStudyGate(listing);
  const n = Number(span);
  const bars = gate.allowed ? barsForBasis(listing, "adjusted").bars : [];
  const events = listing.actions.filter((a) => a.type === "split" || a.type === "cash_dividend").map((a) => ({ date: a.date, label: `${ACTION_LABELS[a.type]} ${a.date}` }));
  const windows = eventWindows(bars, events, n, n);
  const mean = meanWindow(windows);
  const all = windows.flatMap((w) => w.points.map((p) => p.value));
  const ext = extent(all);

  const x = linearScale([-n, n], [8, W - 48]);
  const ticks = ext ? niceTicks(ext[0], ext[1], 4) : [];
  const y = linearScale(ticks.length ? [ticks[0], ticks[ticks.length - 1]] : [90, 110], [H - 24, 8]);
  const path = (pts: { offset: number; value: number }[]) => linePath(pts.map((p) => [x(p.offset), y(p.value)] as const));

  return (
    <ChartFrame
      id="event-study"
      kicker="Event study"
      title="Price path around corporate actions"
      takeaway={
        gate.allowed
          ? windows.length > 0 && mean
            ? `${windows.length} event${windows.length === 1 ? "" : "s"} with a full window; the mean path ends at ${formatNumber(mean[mean.length - 1].value, 1)} (${formatNumber(mean[mean.length - 1].value - 100, 1)} vs the base session).`
            : "No recorded action has a full window inside the series."
          : "Withheld: the adjusted and raw series are not reconciled."
      }
      units={`Adjusted close indexed to 100 at the last session before the event; offset 0 is the event session; ${windows.length} event${windows.length === 1 ? "" : "s"}`}
      hasValue={gate.allowed && windows.length > 0}
      controls={<Segmented label="Window" options={WINDOWS} value={span} onChange={setSpan} />}
      provenance={{
        source: listing.source,
        asOf: bars[bars.length - 1]?.date ?? null,
        method: "Adjusted close; events from the confirmed ledger; windows that run off the series are omitted",
        state: gate.allowed ? (windows.length > 0 ? "verified" : "not_covered") : "unavailable",
        reason: gate.allowed ? (windows.length === 0 ? "No action has enough sessions on both sides." : null) : gate.reason,
      }}
      table={
        mean
          ? {
              caption: "Mean and per-event indexed price by session offset",
              columns: ["Offset", "Mean", ...windows.map((w) => w.label)],
              rows: mean.map((m, i) => [`${m.offset > 0 ? "+" : ""}${m.offset}`, formatNumber(m.value, 2), ...windows.map((w) => formatNumber(w.points[i].value, 2))]),
            }
          : undefined
      }
    >
      <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} role="img" aria-label={`Indexed price path ±${n} sessions around ${windows.length} corporate actions`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={8} x2={W - 48} y1={y(t)} y2={y(t)} className={t === 100 ? "stroke-text-faint" : "stroke-chart-grid"} strokeWidth={1} strokeDasharray={t === 100 ? "3 3" : undefined} shapeRendering="crispEdges" />
            <text x={W} y={y(t) + 4} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
              {formatNumber(t, 1)}
            </text>
          </g>
        ))}
        <line x1={x(0)} x2={x(0)} y1={8} y2={H - 24} className="stroke-text" strokeWidth={1} />
        {Array.from({ length: n * 2 + 1 }, (_, i) => i - n)
          .filter((o) => o % (n > 5 ? 5 : 1) === 0)
          .map((o) => (
            <text key={o} x={x(o)} y={H - 6} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
              {o > 0 ? `+${o}` : o}
            </text>
          ))}
        {windows.map((w) => (
          <path key={w.eventDate} d={path(w.points)} fill="none" className="stroke-chart-benchmark" strokeWidth={1.25} strokeOpacity={0.7}>
            <title>{w.label}</title>
          </path>
        ))}
        {mean ? <path d={path(mean)} fill="none" className="stroke-chart-line" strokeWidth={2.5} strokeLinejoin="round" /> : null}
      </svg>
      <p className="flex flex-wrap gap-x-4 text-[11px] text-text-muted">
        <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="inline-block h-0.5 w-4 bg-chart-line" /> Mean</span>
        <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="inline-block h-0.5 w-4 bg-chart-benchmark" /> Each event</span>
      </p>
    </ChartFrame>
  );
}
