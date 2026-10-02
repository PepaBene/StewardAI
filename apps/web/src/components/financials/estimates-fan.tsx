import { ArrowDownRightIcon, ArrowUpRightIcon, MinusIcon } from "lucide-react";

import { AXIS_TEXT, SVG_BASE } from "@/components/charts/shared";
import { ChartFrame, DataStateBadge } from "@/components/data-state";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { linePath } from "@/lib/charts/path";
import { bandScale, extent, linearScale, niceTicks } from "@/lib/charts/scale";
import { revisionSlope } from "@/lib/financials/logic";
import { FORWARD_LABELS, type EstimateSet, type ForwardKind } from "@/lib/financials/types";
import { formatDate } from "@/lib/format";

const W = 820;
const H = 330;
const OFFSET: Record<Exclude<ForwardKind, "actual">, number> = {
  company_guidance: -26,
  published_analyst_consensus: 0,
  owner_scenario: 26,
  event_calendar_estimate: 40,
};

/**
 * Actual, guidance, consensus, scenario and event-calendar values on one metric axis, each
 * with its own mark so they cannot be confused: actuals are a solid line with dots; guidance
 * a bracketed range; consensus a band with mean (bar) and median (tick) and its analyst count;
 * an owner scenario a dashed line with diamonds; a calendar estimate a hollow circle. A
 * period with no estimate stays empty and says why, and the revision slopes below compare only
 * identical metric/basis/provider snapshots.
 */
export function EstimatesFan({ set }: { set: EstimateSet }) {
  const periods = [...new Set([...set.actuals.map((a) => a.period), ...set.forward.map((f) => f.period), ...set.missing.map((m) => m.period)])].sort();
  const values = [
    ...set.actuals.map((a) => a.value),
    ...set.forward.flatMap((f) => [f.low, f.mean, f.median, f.high]),
  ];
  const ext = extent(values)!;
  const ticks = niceTicks(ext[0], ext[1], 5);
  const y = linearScale([ticks[0], ticks[ticks.length - 1]], [H - 34, 14]);
  const band = bandScale(periods.length, [0, W - 52], { paddingInner: 0.1 });
  const cx = (p: string) => band.x(periods.indexOf(p)) + band.bandwidth / 2;
  const actualPts = set.actuals.map((a) => [cx(a.period), y(a.value)] as const);
  const scenario = set.forward.filter((f) => f.kind === "owner_scenario" && f.mean !== null).sort((a, b) => a.period.localeCompare(b.period));
  const consensus = set.forward.filter((f) => f.kind === "published_analyst_consensus");
  const slopes = consensus.map((c) => ({ c, slope: revisionSlope(set.snapshots, c) }));
  const lastActual = set.actuals[set.actuals.length - 1];
  const guidance = set.forward.find((f) => f.kind === "company_guidance");
  const fy1 = consensus[0];

  return (
    <ChartFrame
      id="estimates"
      kicker="Estimates boundary"
      title={`${set.metric}: what was reported, guided, expected and assumed`}
      takeaway={
        fy1 && guidance
          ? `${fy1.period} consensus mean ${formatNumber(fy1.mean!, 2)} (${fy1.analystCount} analysts, range ${formatNumber(fy1.low!, 2)} to ${formatNumber(fy1.high!, 2)}) sits ${fy1.mean! >= guidance.low! && fy1.mean! <= guidance.high! ? "inside" : "outside"} company guidance of ${formatNumber(guidance.low!, 2)} to ${formatNumber(guidance.high!, 2)}. Last actual: ${formatNumber(lastActual.value, 2)} (${lastActual.period}).`
          : "No forward estimate is available for this metric."
      }
      units={`${set.unit}; five kinds of figure, never merged; analyst count shown only where the provider states it`}
      provenance={{
        source: [...new Set(set.forward.map((f) => f.provider))].join(" · "),
        asOf: set.forward.map((f) => f.vintage).sort().pop() ?? null,
        method: "Consensus from company-published files only; the app never fills a missing estimate with a model figure",
        state: set.missing.length > 0 ? "partial" : "verified",
        reason: set.missing[0]?.reason ?? null,
        coverage: { covered: periods.length - set.missing.length, total: periods.length, unit: "periods with any figure" },
      }}
      table={{
        caption: `${set.metric}: every figure by period and kind`,
        columns: ["Period", "Kind", "Low", "Mean", "Median", "High", "Analysts", "Basis", "Vintage"],
        rows: [
          ...set.actuals.map((a) => [a.period, FORWARD_LABELS.actual, "—", formatNumber(a.value, 2), "—", "—", "—", "as filed", "—"]),
          ...set.forward.map((f) => [
            f.period,
            FORWARD_LABELS[f.kind],
            f.low === null ? "—" : formatNumber(f.low, 2),
            f.mean === null ? "—" : formatNumber(f.mean, 2),
            f.median === null ? "—" : formatNumber(f.median, 2),
            f.high === null ? "—" : formatNumber(f.high, 2),
            f.analystCount === null ? "not stated" : f.analystCount,
            f.basis,
            formatDate(f.vintage),
          ]),
          ...set.missing.map((m) => [m.period, "No figure", "—", "—", "—", "—", "—", m.reason, "—"]),
        ],
      }}
    >
      <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} role="img" aria-label={`${set.metric} by period: ${set.actuals.length} actuals, then guidance, consensus, owner scenario and calendar estimates`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={0} x2={W - 52} y1={y(t)} y2={y(t)} className="stroke-chart-grid" strokeWidth={1} shapeRendering="crispEdges" />
            <text x={W} y={y(t) + 4} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
              {formatNumber(t, 2)}
            </text>
          </g>
        ))}
        {periods.map((p) => (
          <text key={p} x={cx(p)} y={H - 10} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
            {p}
            {set.actuals.some((a) => a.period === p) ? "A" : "E"}
          </text>
        ))}
        {set.missing.map((m) => (
          <g key={m.period}>
            <rect x={cx(m.period) - 30} y={14} width={60} height={H - 48} className="fill-none stroke-border" strokeDasharray="3 3" />
            <text x={cx(m.period)} y={H / 2 - 8} textAnchor="middle" fontSize={11} className="fill-text-muted">
              No consensus
            </text>
            <text x={cx(m.period)} y={H / 2 + 8} textAnchor="middle" fontSize={10} className="fill-text-faint">
              {m.state === "paid_only" ? "paid only" : m.state.replaceAll("_", " ")}
            </text>
          </g>
        ))}
        <path d={linePath(actualPts)} fill="none" className="stroke-text" strokeWidth={2} strokeLinejoin="round" />
        {set.actuals.map((a) => (
          <circle key={a.period} cx={cx(a.period)} cy={y(a.value)} r={4} className="fill-text">
            <title>{`${a.period} actual: ${formatNumber(a.value, 2)}`}</title>
          </circle>
        ))}
        {scenario.length > 1 ? <path d={linePath(scenario.map((s) => [cx(s.period) + OFFSET.owner_scenario, y(s.mean!)] as const))} fill="none" className="stroke-text-muted" strokeWidth={1.5} strokeDasharray="5 4" /> : null}
        {set.forward.map((f, i) => {
          const x = cx(f.period) + OFFSET[f.kind];
          if (f.kind === "company_guidance" && f.low !== null && f.high !== null) {
            return (
              <g key={i}>
                <line x1={x} x2={x} y1={y(f.high)} y2={y(f.low)} className="stroke-text" strokeWidth={2} />
                <line x1={x - 5} x2={x + 5} y1={y(f.high)} y2={y(f.high)} className="stroke-text" strokeWidth={2} />
                <line x1={x - 5} x2={x + 5} y1={y(f.low)} y2={y(f.low)} className="stroke-text" strokeWidth={2} />
                <text x={x} y={y(f.high) - 6} textAnchor="middle" fontSize={10} className="fill-text font-semibold">G</text>
                <title>{`${f.period} company guidance ${formatNumber(f.low, 2)} to ${formatNumber(f.high, 2)} (${f.provider}, ${formatDate(f.vintage)})`}</title>
              </g>
            );
          }
          if (f.kind === "published_analyst_consensus" && f.low !== null && f.high !== null && f.mean !== null) {
            return (
              <g key={i}>
                <rect x={x - 11} y={y(f.high)} width={22} height={Math.max(1, y(f.low) - y(f.high))} className="fill-accent-soft stroke-accent" strokeWidth={1.25}>
                  <title>{`${f.period} consensus ${formatNumber(f.low, 2)} to ${formatNumber(f.high, 2)}, mean ${formatNumber(f.mean, 2)}, ${f.analystCount ?? "unknown"} analysts (${f.provider}, ${formatDate(f.vintage)})`}</title>
                </rect>
                <line x1={x - 14} x2={x + 14} y1={y(f.mean)} y2={y(f.mean)} className="stroke-accent-ramp-1" strokeWidth={3} />
                {f.median !== null ? <line x1={x - 5} x2={x + 5} y1={y(f.median)} y2={y(f.median)} className="stroke-text" strokeWidth={1.5} /> : null}
                <text x={x} y={y(f.high) - 6} textAnchor="middle" fontSize={10} className="fill-text-muted num">
                  {f.analystCount === null ? "n n/a" : `n=${f.analystCount}`}
                </text>
              </g>
            );
          }
          if (f.kind === "owner_scenario" && f.mean !== null) {
            return (
              <g key={i}>
                {f.low !== null && f.high !== null ? <line x1={x} x2={x} y1={y(f.high)} y2={y(f.low)} className="stroke-text-muted" strokeWidth={1} strokeDasharray="1 3" /> : null}
                <rect x={x - 4} y={y(f.mean) - 4} width={8} height={8} transform={`rotate(45 ${x} ${y(f.mean)})`} className="fill-surface-raised stroke-text-muted" strokeWidth={1.5}>
                  <title>{`${f.period} owner scenario ${formatNumber(f.mean, 2)} (${f.provider})`}</title>
                </rect>
              </g>
            );
          }
          if (f.kind === "event_calendar_estimate" && f.mean !== null) {
            return (
              <circle key={i} cx={x} cy={y(f.mean)} r={4.5} className="fill-surface-raised stroke-text" strokeWidth={1.5}>
                <title>{`${f.period} event-calendar estimate ${formatNumber(f.mean, 2)} (${f.provider}, ${f.basis}); no analyst count`}</title>
              </circle>
            );
          }
          return null;
        })}
      </svg>
      <ul className="m-0 flex list-none flex-wrap gap-x-5 gap-y-1 p-0 text-[11px] text-text-muted">
        <li className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="inline-block h-0.5 w-4 bg-text" /> Actual (A)</li>
        <li className="inline-flex items-center gap-1.5"><b aria-hidden="true" className="font-semibold text-text">G</b> Company guidance range</li>
        <li className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="inline-block h-3 w-3 border border-accent bg-accent-soft" /> Consensus range, bar = mean, tick = median, n = analysts</li>
        <li className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="inline-block size-2 rotate-45 border border-text-muted" /> Owner scenario</li>
        <li className="inline-flex items-center gap-1.5"><i aria-hidden="true" className="inline-block size-2.5 rounded-full border border-text" /> Event-calendar estimate</li>
      </ul>

      <section aria-labelledby="revisions-heading" className="flex flex-col gap-1.5">
        <h3 id="revisions-heading" className="font-heading text-lg font-semibold">
          Consensus revisions between comparable snapshots
        </h3>
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-sm">
          {slopes.map(({ c, slope }) => (
            <li key={c.period} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 border-b border-border-soft pb-1.5">
              <span className="w-14 font-medium">{c.period}</span>
              {slope ? (
                <>
                  {slope.change > 0 ? <ArrowUpRightIcon aria-hidden="true" className="size-4" /> : slope.change < 0 ? <ArrowDownRightIcon aria-hidden="true" className="size-4" /> : <MinusIcon aria-hidden="true" className="size-4" />}
                  <span className="num">
                    {formatNumber(slope.from.mean, 2)} → {formatNumber(slope.to.mean, 2)}
                  </span>
                  <span className="num font-semibold">{slope.pct === null ? "" : formatPct(slope.pct, 1, true)}</span>
                  <span className="text-xs text-text-muted">
                    {formatDate(slope.from.vintage)} to {formatDate(slope.to.vintage)} ({slope.days} days, same basis and provider)
                  </span>
                </>
              ) : (
                <span className="text-xs text-text-muted">One comparable snapshot only: an earlier snapshot used a different basis and is not joined, so no revision is shown.</span>
              )}
            </li>
          ))}
        </ul>
      </section>
      {set.missing.map((m) => (
        <p key={m.period} className="flex flex-wrap items-center gap-2 text-sm">
          <DataStateBadge state={m.state} />
          <span>
            <strong className="font-medium">{m.period}:</strong> {m.reason}
          </span>
        </p>
      ))}
    </ChartFrame>
  );
}
