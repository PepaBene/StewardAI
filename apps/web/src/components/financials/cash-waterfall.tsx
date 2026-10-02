import { AXIS_TEXT, SVG_BASE } from "@/components/charts/shared";
import { ChartFrame } from "@/components/data-state";
import { formatCompact, formatNumber, formatPct, formatSigned } from "@/lib/charts/format";
import { bandScale, extent, linearScale, niceTicks } from "@/lib/charts/scale";
import { cashWalk } from "@/lib/financials/logic";
import type { CashWalk } from "@/lib/financials/types";
import { cn } from "@/lib/utils";

const W = 760;
const H = 300;

/**
 * Net income → operating cash flow → free cash flow as a waterfall. Totals are solid bars from
 * zero, changes float between levels (increases in the accent, decreases outlined, each with a
 * sign), and the walk is reconciled to the filed totals: a mismatch is stated, not hidden.
 */
export function CashWaterfall({ walk, source, sourceUrl }: { walk: CashWalk; source: string; sourceUrl: string | null }) {
  const r = cashWalk(walk);
  const all = r.steps.flatMap((s) => [s.from, s.to]);
  const ext = extent([...all, 0])!;
  const ticks = niceTicks(ext[0], ext[1], 5);
  const y = linearScale([ticks[0], ticks[ticks.length - 1]], [H - 56, 12]);
  const band = bandScale(r.steps.length, [0, W - 56], { paddingInner: 0.3, maxBandwidth: 64 });
  const fmt = (v: number) => formatCompact(v);

  return (
    <ChartFrame
      id="cash-walk"
      kicker="Cash conversion"
      title={`From net income to free cash flow, ${walk.period}`}
      takeaway={
        r.fcfConversion !== null
          ? `${formatPct(r.fcfConversion, 0)} of net income became free cash flow (${fmt(walk.reportedFcf)} of ${fmt(walk.netIncome)} ${walk.currency}m); working capital absorbed ${fmt(Math.abs(walk.adjustments.find((a) => a.label === "Working capital")?.value ?? 0))}.`
          : "Net income is not positive, so a conversion ratio is not defined."
      }
      units={`${walk.currency} millions, ${walk.period}; operating cash flow ${r.cfoConversion !== null ? formatPct(r.cfoConversion, 0) : "n/a"} of net income`}
      provenance={{
        source,
        sourceUrl,
        asOf: `${walk.period.replace("FY", "")}-12-31`,
        method: "Filed statements; each step is a filed line, and the walk is reconciled to reported operating cash flow and free cash flow",
        state: r.reconciles ? "verified" : "disputed",
        reason: r.reconciles ? null : `The steps differ from the filed totals by ${fmt(r.cfoGap)} (operating cash flow) and ${fmt(r.fcfGap)} (free cash flow). Do not use this walk for valuation or peers.`,
      }}
      table={{
        caption: `Cash walk ${walk.period}`,
        columns: ["Step", `Change or level (${walk.currency}m)`, "Running level"],
        rows: r.steps.map((s) => [s.label, s.kind === "delta" ? formatSigned(s.value, 0) : formatNumber(s.value, 0), formatNumber(s.to, 0)]),
      }}
    >
      <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} role="img" aria-label={`Cash walk ${walk.period}: ${r.steps.map((s) => `${s.label} ${formatNumber(s.value, 0)}`).join(", ")}`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={0} x2={W - 56} y1={y(t)} y2={y(t)} className={t === 0 ? "stroke-text-faint" : "stroke-chart-grid"} strokeWidth={1} shapeRendering="crispEdges" />
            <text x={W} y={y(t) + 4} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
              {fmt(t)}
            </text>
          </g>
        ))}
        {r.steps.map((s, i) => {
          const bx = band.x(i);
          const cx = bx + band.bandwidth / 2;
          const lo = Math.min(s.from, s.to);
          const hi = Math.max(s.from, s.to);
          const total = s.kind !== "delta";
          const up = s.value >= 0;
          const next = r.steps[i + 1];
          return (
            <g key={s.label}>
              <rect
                x={bx}
                y={y(hi)}
                width={band.bandwidth}
                height={Math.max(1, y(lo) - y(hi))}
                className={cn(total ? "fill-accent-ramp-1" : up ? "fill-accent" : "fill-surface-raised stroke-text")}
                strokeWidth={total || up ? 0 : 1.5}
                strokeDasharray={!total && !up ? "4 2" : undefined}
              >
                <title>{`${s.label}: ${total ? formatNumber(s.value, 0) : formatSigned(s.value, 0)} ${walk.currency}m`}</title>
              </rect>
              {next && s.kind !== "end" ? (
                <line x1={bx + band.bandwidth} x2={band.x(i + 1)} y1={y(s.to)} y2={y(s.to)} className="stroke-text-faint" strokeWidth={1} strokeDasharray="2 2" />
              ) : null}
              <text x={cx} y={y(hi) - 5} textAnchor="middle" fontSize={11} className="fill-text num font-semibold">
                {total ? fmt(s.value) : `${up ? "+" : "−"}${fmt(Math.abs(s.value))}`}
              </text>
              <text x={cx} y={H - 28} textAnchor="middle" fontSize={11} className="fill-text-muted">
                {s.label.length > 16 ? [s.label.slice(0, s.label.lastIndexOf(" ", 16)), s.label.slice(s.label.lastIndexOf(" ", 16) + 1)].map((t, k) => (
                  <tspan key={k} x={cx} dy={k === 0 ? 0 : 13}>
                    {t}
                  </tspan>
                )) : s.label}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
        <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="inline-block size-2.5 bg-accent-ramp-1" /> Level (starts at zero)</span>
        <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="inline-block size-2.5 bg-accent" /> Increase (+)</span>
        <span className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="inline-block size-2.5 border border-dashed border-text" /> Decrease (−)</span>
        <span className="num">Reconciled to filed totals: {r.reconciles ? "yes" : "no"}</span>
      </p>
    </ChartFrame>
  );
}
