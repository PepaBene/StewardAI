import { AXIS_TEXT, SVG_BASE } from "@/components/charts/shared";
import { ChartFrame } from "@/components/data-state";
import { formatCompact, formatNumber, formatPct, formatSigned } from "@/lib/charts/format";
import { bandScale, extent, linearScale, niceTicks } from "@/lib/charts/scale";
import { cashWalk, safeHref, type CashWalkResult } from "@/lib/financials/logic";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

const W = 760;
const H = 300;

/**
 * Net income → filed adjustments → operating cash flow → free cash flow, drawn as a waterfall
 * ONLY when net income plus the filed adjustment lines equals the filed operating cash flow.
 * The stored facts rarely itemise working capital, so usually the walk does not close: then
 * the filed figures and the unexplained gap are shown as a table and no waterfall is drawn
 * (a bar for "other" would be a number nobody filed). Free cash flow is derived (CFO − capex).
 */
export function CashWaterfall({ result }: { result: CashWalkResult }) {
  if (!result.walk) {
    return (
      <ChartFrame
        id="cash-walk"
        kicker="Cash conversion"
        title="From net income to free cash flow"
        provenance={{ source: "Filed cash-flow and income statements (stored XBRL facts)", asOf: null, state: "not_covered", reason: result.reason }}
        hasValue={false}
      >
        {null}
      </ChartFrame>
    );
  }
  const walk = result.walk;
  const r = cashWalk(walk);
  const fmt = (v: number) => formatCompact(v);
  const cur = walk.currency;
  const common = {
    id: "cash-walk",
    kicker: "Cash conversion",
    title: `From net income to free cash flow, ${walk.label}`,
  };
  const provenanceBase = {
    source: `${walk.source ?? "Filed statements"}${walk.accessions.length ? ` · ${walk.accessions.join(", ")}` : ""}`,
    sourceUrl: safeHref(walk.sourceUrl),
    asOf: walk.periodEnd,
    knownAt: walk.knownAt,
    method: "Each step is a filed line; free cash flow = filed operating cash flow − filed capital expenditure (derived)",
  };

  if (!r.reconciles) {
    const rows: [string, string][] = [
      ["Net income (filed)", formatNumber(walk.netIncome, 0)],
      ...walk.adjustments.map((a): [string, string] => [`${a.label} (filed)`, formatSigned(a.value, 0)]),
      ["Net income + filed adjustments", formatNumber(r.derivedCfo, 0)],
      ["Operating cash flow (filed)", formatNumber(walk.cfo, 0)],
      ["Not explained by the stored lines", formatSigned(-r.cfoGap, 0)],
      ...(walk.capex !== null ? ([["Capital expenditure (filed)", formatSigned(-walk.capex, 0)]] as [string, string][]) : []),
      ...(r.fcf !== null ? ([["Free cash flow (derived)", formatNumber(r.fcf, 0)]] as [string, string][]) : []),
    ];
    return (
      <ChartFrame
        {...common}
        takeaway={`The stored lines explain ${formatPct(Math.max(0, 1 - Math.abs(r.cfoGap) / Math.max(Math.abs(walk.cfo), 1)), 0)} of the step from net income to filed operating cash flow; the rest (working capital and other items) is not stored, so no waterfall is drawn.`}
        units={`${cur}, ${walk.label} (${formatDate(walk.periodStart)} to ${formatDate(walk.periodEnd)}); operating cash flow ${r.cfoConversion !== null ? formatPct(r.cfoConversion, 0) : "n/a"} of net income`}
        provenance={{
          ...provenanceBase,
          state: "partial",
          reason: `Net income plus the filed adjustments differs from filed operating cash flow by ${fmt(Math.abs(r.cfoGap))} ${cur}. The gap is not a filed fact and is not drawn as one; do not use this walk for valuation or peers.`,
        }}
      >
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[20rem] text-sm">
            <caption className="sr-only">Cash walk {walk.label}: filed figures and the unexplained gap</caption>
            <tbody>
              {rows.map(([label, value]) => (
                <tr key={label} className={cn("border-b border-border-soft", label.startsWith("Not explained") && "bg-warning-signal-subtle")}>
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {label}
                  </th>
                  <td className="num px-3 py-1.5 text-right">{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </ChartFrame>
    );
  }

  const all = r.steps.flatMap((s) => [s.from, s.to]);
  const ext = extent([...all, 0]) as [number, number];
  const ticks = niceTicks(ext[0], ext[1], 5);
  const y = linearScale([ticks[0], ticks[ticks.length - 1]], [H - 56, 12]);
  const band = bandScale(r.steps.length, [0, W - 56], { paddingInner: 0.3, maxBandwidth: 64 });
  return (
    <ChartFrame
      {...common}
      takeaway={
        r.fcfConversion !== null && r.fcf !== null
          ? `${formatPct(r.fcfConversion, 0)} of net income became free cash flow (${fmt(r.fcf)} of ${fmt(walk.netIncome)} ${cur}).`
          : r.cfoConversion !== null
            ? `Operating cash flow was ${formatPct(r.cfoConversion, 0)} of net income; no capital expenditure is stored, so free cash flow is not derived.`
            : "Net income is not positive, so a conversion ratio is not defined."
      }
      units={`${cur}, ${walk.label}; the walk reconciles to filed operating cash flow`}
      provenance={{ ...provenanceBase, state: "verified" }}
      table={{
        caption: `Cash walk ${walk.label}`,
        columns: ["Step", `Change or level (${cur})`, "Running level"],
        rows: r.steps.map((s) => [s.label, s.kind === "delta" ? formatSigned(s.value, 0) : formatNumber(s.value, 0), formatNumber(s.to, 0)]),
      }}
    >
      <div className="relative overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className={cn(SVG_BASE, "min-w-[34rem]")} role="img" aria-label={`Cash walk ${walk.label}: ${r.steps.map((s) => `${s.label} ${formatNumber(s.value, 0)}`).join(", ")}`}>
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
            const words = s.label.length > 16 ? [s.label.slice(0, s.label.lastIndexOf(" ", 16)), s.label.slice(s.label.lastIndexOf(" ", 16) + 1)] : [s.label];
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
                  <title>{`${s.label}: ${total ? formatNumber(s.value, 0) : formatSigned(s.value, 0)} ${cur}`}</title>
                </rect>
                {next && s.kind !== "end" ? <line x1={bx + band.bandwidth} x2={band.x(i + 1)} y1={y(s.to)} y2={y(s.to)} className="stroke-text-faint" strokeWidth={1} strokeDasharray="2 2" /> : null}
                <text x={cx} y={y(hi) - 5} textAnchor="middle" fontSize={11} className="fill-text num font-semibold">
                  {total ? fmt(s.value) : `${up ? "+" : "−"}${fmt(Math.abs(s.value))}`}
                </text>
                <text x={cx} y={H - 28} textAnchor="middle" fontSize={11} className="fill-text-muted">
                  {words.map((t, k) => (
                    <tspan key={k} x={cx} dy={k === 0 ? 0 : 13}>
                      {t}
                    </tspan>
                  ))}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
        <span className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block size-2.5 bg-accent-ramp-1" /> Level (starts at zero)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block size-2.5 bg-accent" /> Increase (+)
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i aria-hidden="true" className="inline-block size-2.5 border border-dashed border-text" /> Decrease (−)
        </span>
      </p>
    </ChartFrame>
  );
}
