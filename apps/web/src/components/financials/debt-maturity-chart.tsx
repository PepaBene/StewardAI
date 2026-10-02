import { AXIS_TEXT, CATEGORICAL_FILL, SVG_BASE } from "@/components/charts/shared";
import { ChartFrame } from "@/components/data-state";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { bandScale, linearScale, niceTicks } from "@/lib/charts/scale";
import { debtBreakdown } from "@/lib/financials/logic";
import type { DebtSchedule } from "@/lib/financials/types";

const W = 760;
const H = 270;
const CAT = [0, 3, 5, 1];

/**
 * Debt maturity ladder: stacked bars by maturity year, one segment per currency (expressed in
 * the reporting currency), and a separate dashed "Unclassified" bar for the part of reported
 * debt the maturity table does not explain. Nothing is spread over years to fill the gap.
 */
export function DebtMaturityChart({ schedule, sourceUrl }: { schedule: DebtSchedule; sourceUrl: string | null }) {
  const b = debtBreakdown(schedule);
  const cols = [...b.buckets.map((x) => ({ key: x.key, label: x.label, total: x.total, byCurrency: x.byCurrency, un: false })), ...(b.unclassified > 0 ? [{ key: "un", label: "Unclassified", total: b.unclassified, byCurrency: {} as Record<string, number>, un: true }] : [])];
  const max = Math.max(...cols.map((c) => c.total), 1);
  const ticks = niceTicks(0, max, 4);
  const y = linearScale([0, ticks[ticks.length - 1]], [H - 30, 10]);
  const band = bandScale(cols.length, [0, W - 52], { paddingInner: 0.28, maxBandwidth: 70 });
  const cur = schedule.reportingCurrency;
  const nearTerm = b.buckets.slice(0, 2).reduce((s, x) => s + x.total, 0);

  return (
    <ChartFrame
      id="debt-maturity"
      kicker="Debt maturity"
      title="When debt falls due, by year and currency"
      takeaway={`${formatPct(nearTerm / schedule.reportedTotal, 0)} of reported debt matures within two years (${formatCompact(nearTerm)} ${cur}m); ${b.unclassified > 0 ? `${formatPct(b.unclassifiedShare ?? 0, 0)} (${formatCompact(b.unclassified)}) is not assigned a year in the filed maturity table.` : "every reported dollar is assigned a year."}`}
      units={`${cur} millions at the filed FX rates; reported total debt ${formatCompact(schedule.reportedTotal)}; maturity table explains ${formatPct(b.classified / schedule.reportedTotal, 0)}`}
      provenance={{
        source: schedule.citation,
        sourceUrl,
        asOf: schedule.asOf,
        method: "Maturity table from the debt note; non-reporting currencies converted at the stated rates; remainder shown as Unclassified",
        state: b.overClassified ? "disputed" : b.unclassified > 0 ? "partial" : "verified",
        reason: b.overClassified ? "The maturity table sums to more than reported debt: check the extraction." : b.unclassified > 0 ? "Part of reported debt (revolver draws, leases or finance obligations) has no maturity year in the table." : null,
        coverage: { covered: Math.round(b.classified), total: Math.round(schedule.reportedTotal), unit: `${cur}m of reported debt` },
      }}
      table={{
        caption: "Debt maturities by year and currency",
        columns: ["Year", ...b.currencies, `Total (${cur}m)`],
        rows: [
          ...b.buckets.map((x) => [x.label, ...b.currencies.map((c) => (x.byCurrency[c] ? formatCompact(x.byCurrency[c]) : "—")), formatCompact(x.total)]),
          ["Unclassified", ...b.currencies.map(() => "—"), formatCompact(b.unclassified)],
        ],
      }}
    >
      <svg viewBox={`0 0 ${W} ${H}`} className={SVG_BASE} role="img" aria-label={`Debt maturities: ${cols.map((c) => `${c.label} ${formatCompact(c.total)}`).join(", ")} ${cur} millions`}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={0} x2={W - 52} y1={y(t)} y2={y(t)} className="stroke-chart-grid" strokeWidth={1} shapeRendering="crispEdges" />
            <text x={W} y={y(t) + 4} textAnchor="end" fontSize={11} className={AXIS_TEXT}>
              {formatCompact(t)}
            </text>
          </g>
        ))}
        {cols.map((c, i) => {
          const bx = band.x(i);
          let acc = 0;
          return (
            <g key={c.key}>
              {c.un ? (
                <rect x={bx} y={y(c.total)} width={band.bandwidth} height={y(0) - y(c.total)} className="fill-none stroke-text-muted" strokeWidth={1.5} strokeDasharray="4 3">
                  <title>{`Unclassified: ${formatCompact(c.total)} ${cur}m, no maturity year in the filed table`}</title>
                </rect>
              ) : (
                b.currencies.map((cc) => {
                  const v = c.byCurrency[cc] ?? 0;
                  if (v <= 0) return null;
                  const seg = (
                    <rect key={cc} x={bx} y={y(acc + v)} width={band.bandwidth} height={Math.max(1, y(acc) - y(acc + v))} className={CATEGORICAL_FILL[CAT[b.currencies.indexOf(cc) % CAT.length]]} stroke="none">
                      <title>{`${c.label} · ${cc}: ${formatCompact(v)} ${cur}m`}</title>
                    </rect>
                  );
                  acc += v;
                  return seg;
                })
              )}
              <text x={bx + band.bandwidth / 2} y={y(c.total) - 5} textAnchor="middle" fontSize={11} className="fill-text num font-semibold">
                {formatCompact(c.total)}
              </text>
              <text x={bx + band.bandwidth / 2} y={H - 10} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
                {c.label}
              </text>
            </g>
          );
        })}
      </svg>
      <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-[11px] text-text-muted">
        {b.currencies.map((c, i) => (
          <li key={c} className="inline-flex items-center gap-1.5">
            <i aria-hidden="true" className={`inline-block size-2.5 ${["bg-categorical-1", "bg-categorical-4", "bg-categorical-6", "bg-categorical-2"][i % 4]}`} />
            {c}
            {c !== cur ? <span className="num">(× {schedule.fxToReporting[c]} to {cur})</span> : null}
          </li>
        ))}
        {b.unclassified > 0 ? (
          <li className="inline-flex items-center gap-1.5">
            <i aria-hidden="true" className="inline-block size-2.5 border border-dashed border-text-muted" />
            Unclassified
          </li>
        ) : null}
      </ul>
    </ChartFrame>
  );
}
