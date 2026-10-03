"use client";

import { useState } from "react";

import { AXIS_TEXT, SVG_BASE } from "@/components/charts/shared";
import { ChartFrame, Segmented } from "@/components/data-state";
import { formatNumber } from "@/lib/charts/format";
import { linePath } from "@/lib/charts/path";
import { extent, linearScale, niceTicks } from "@/lib/charts/scale";
import { eventWindows, meanWindow, type EventStudyGate } from "@/lib/markets/logic";
import { ACTION_LABELS, type CorporateAction } from "@/lib/markets/types";

const W = 640;
const H = 240;

const WINDOWS = [
  { id: "5", label: "±5 sessions" },
  { id: "10", label: "±10 sessions" },
] as const;

/**
 * Indexed price path around each confirmed split and cash dividend (100 = last close before the
 * event), faint per event with their mean in the accent. It exists only where the backend says
 * the adjusted series is reconciled: otherwise a split would read as a -75% "event reaction", so
 * the gate's reason replaces the chart.
 */
export function EventStudyPanel({
  ticker,
  source,
  bars,
  actions,
  gate,
}: {
  ticker: string;
  source: string;
  bars: readonly { date: string; close: number | null }[];
  actions: readonly CorporateAction[];
  gate: EventStudyGate;
}) {
  const [span, setSpan] = useState<(typeof WINDOWS)[number]["id"]>("5");
  const n = Number(span);
  const events = actions
    .filter((a) => a.status === "confirmed" && (a.type === "split" || a.type === "cash_dividend"))
    .map((a) => ({ date: a.exDate, label: `${ACTION_LABELS[a.type]} ${a.exDate}` }));
  const windows = gate.allowed ? eventWindows(bars, events, n, n) : [];
  const mean = meanWindow(windows);
  const ext = extent(windows.flatMap((w) => w.points.map((p) => p.value)));
  const x = linearScale([-n, n], [8, W - 48]);
  const ticks = ext ? niceTicks(ext[0], ext[1], 4) : [];
  const y = linearScale(ticks.length ? [ticks[0], ticks[ticks.length - 1]] : [90, 110], [H - 24, 8]);
  const path = (pts: { offset: number; value: number }[]) => linePath(pts.map((p) => [x(p.offset), y(p.value)] as const));
  const last = mean ? mean[mean.length - 1] : null;

  return (
    <ChartFrame
      id="event-study"
      kicker="Event study"
      title={`${ticker}: price path around corporate actions`}
      takeaway={
        !gate.allowed
          ? "Withheld: the adjusted series is not reconciled against the corporate-action ledger."
          : last
            ? `${windows.length} confirmed event${windows.length === 1 ? "" : "s"} with a full window; the mean path ends ${formatNumber(last.value - 100, 1)} points from the base session.`
            : `${events.length} confirmed event${events.length === 1 ? "" : "s"} in the ledger, none with ${n} sessions of stored bars on both sides.`
      }
      units={`Adjusted close indexed to 100 at the last session before the event; offset 0 is the ex-date session; ${windows.length} event${windows.length === 1 ? "" : "s"} drawn`}
      hasValue={gate.allowed && windows.length > 0}
      controls={gate.allowed ? <Segmented label="Window" options={WINDOWS} value={span} onChange={setSpan} /> : null}
      provenance={{
        source,
        asOf: bars[bars.length - 1]?.date ?? null,
        method: "Adjusted closes; events are confirmed ledger entries; windows that run off the stored series are omitted",
        state: gate.allowed ? (windows.length > 0 ? "verified" : "not_covered") : "not_covered",
        reason: gate.allowed ? (windows.length === 0 ? "No confirmed split or dividend has enough stored sessions on both sides." : null) : gate.reason,
        coverage: gate.allowed ? { covered: windows.length, total: events.length, unit: "confirmed events" } : null,
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
      <div className="relative overflow-x-auto">
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
            <path key={w.eventDate + w.label} d={path(w.points)} fill="none" className="stroke-chart-benchmark" strokeWidth={1.25} strokeOpacity={0.7}>
              <title>{w.label}</title>
            </path>
          ))}
          {mean ? <path d={path(mean)} fill="none" className="stroke-chart-line" strokeWidth={2.5} strokeLinejoin="round" /> : null}
        </svg>
      </div>
      <p className="flex flex-wrap gap-x-4 text-[11px] text-text-muted">
        <span className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block h-0.5 w-4 bg-chart-line" /> Mean
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block h-0.5 w-4 bg-chart-benchmark" /> Each event
        </span>
      </p>
    </ChartFrame>
  );
}
