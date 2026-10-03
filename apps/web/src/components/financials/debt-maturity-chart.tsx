import { AXIS_TEXT, SVG_BASE } from "@/components/charts/shared";
import { ChartFrame } from "@/components/data-state";
import { formatCompact, formatPct } from "@/lib/charts/format";
import { bandScale, linearScale, niceTicks } from "@/lib/charts/scale";
import { debtBreakdown } from "@/lib/financials/logic";
import type { DebtSchedule } from "@/lib/financials/types";
import { cn } from "@/lib/utils";

const W = 760;
const H = 270;

/**
 * Debt maturity ladder from the filed debt note: one bar per filed maturity bucket (relative
 * to the balance date, as the filing states them), and a separate dashed "Unclassified" bar
 * for the part of balance-sheet debt the maturity table does not explain. Nothing is spread
 * over years to fill the gap. Lease obligations stay a table: they are a different obligation.
 */
export function DebtMaturityChart({ schedule }: { schedule: DebtSchedule | null }) {
  if (!schedule) {
    return (
      <ChartFrame
        id="debt-maturity"
        kicker="Debt maturity"
        title="When debt falls due"
        provenance={{ source: "Filed debt note (stored XBRL facts)", asOf: null, state: "not_covered", reason: "No debt-maturity facts are stored for this holding (the filing does not tag them, or it is not an XBRL filer)." }}
        hasValue={false}
      >
        {null}
      </ChartFrame>
    );
  }
  const b = debtBreakdown(schedule);
  const cur = schedule.currency;
  const cols = [
    ...schedule.tranches.map((t) => ({ key: t.key, label: t.label, total: t.amount, un: false })),
    ...(b.unclassified > 0 ? [{ key: "un", label: "Unclassified", total: b.unclassified, un: true }] : []),
  ];
  const max = Math.max(...cols.map((c) => c.total), 1);
  const ticks = niceTicks(0, max, 4);
  const y = linearScale([0, ticks[ticks.length - 1]], [H - 44, 10]);
  const band = bandScale(cols.length, [0, W - 52], { paddingInner: 0.28, maxBandwidth: 70 });
  const near = schedule.tranches.filter((t) => t.key === "debt_maturity_y1" || t.key === "debt_maturity_remainder").reduce((s, t) => s + t.amount, 0);
  const total = b.reportedTotal;

  const state = b.overClassified ? "disputed" : total === null ? "partial" : b.reconciles ? "verified" : "partial";
  const reason = b.overClassified
    ? "The maturity table sums to more than balance-sheet debt by more than 2%: check the extraction before using it."
    : total === null
      ? "The filing facts give no balance-sheet debt total for this date, so the table cannot be checked against it."
      : b.unclassified > 0
        ? "Part of balance-sheet debt (revolver draws, commercial paper or finance obligations) has no maturity bucket in the filed table."
        : null;

  return (
    <ChartFrame
      id="debt-maturity"
      kicker="Debt maturity"
      title="When debt falls due"
      takeaway={`${total !== null && total > 0 ? `${formatPct(near / total, 0)} of balance-sheet debt falls due within 12 months (${formatCompact(near)} ${cur}); ` : `${formatCompact(near)} ${cur} falls due within 12 months; `}${b.unclassified > 0 ? `${formatPct(b.unclassifiedShare ?? 0, 0)} (${formatCompact(b.unclassified)}) has no maturity bucket in the filed table.` : total === null ? "balance-sheet debt is not available to compare." : "the maturity table agrees with balance-sheet debt."}`}
      units={`${cur}, as of ${schedule.periodEnd}; maturity table sums to ${formatCompact(b.classified)}${total !== null ? ` against ${formatCompact(total)} reported debt (${schedule.totalBasis})` : ""}`}
      provenance={{
        source: schedule.source ?? "Filed debt note",
        sourceUrl: schedule.sourceUrl,
        asOf: schedule.periodEnd,
        knownAt: schedule.knownAt,
        method: "Principal maturities from the debt note, buckets as filed (relative to the balance date); remainder against balance-sheet debt shown as Unclassified",
        state,
        reason,
        coverage: total !== null && total > 0 ? { covered: Math.round(Math.min(b.classified, total)), total: Math.round(total), unit: `${cur} of reported debt` } : null,
      }}
      table={{
        caption: "Debt maturities as filed",
        columns: ["Bucket", `Amount (${cur})`],
        rows: [...schedule.tranches.map((t) => [t.label, formatCompact(t.amount)]), ...(b.unclassified > 0 ? [["Unclassified", formatCompact(b.unclassified)]] : [])],
      }}
    >
      <div className="relative overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className={cn(SVG_BASE, "min-w-[30rem]")} role="img" aria-label={`Debt maturities: ${cols.map((c) => `${c.label} ${formatCompact(c.total)}`).join(", ")} ${cur}`}>
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
            const cx = bx + band.bandwidth / 2;
            const words = c.label.length > 12 ? [c.label.slice(0, c.label.lastIndexOf(" ", 12)), c.label.slice(c.label.lastIndexOf(" ", 12) + 1)] : [c.label];
            return (
              <g key={c.key}>
                {c.un ? (
                  <rect x={bx} y={y(c.total)} width={band.bandwidth} height={y(0) - y(c.total)} className="fill-none stroke-text-muted" strokeWidth={1.5} strokeDasharray="4 3">
                    <title>{`Unclassified: ${formatCompact(c.total)} ${cur}, no maturity bucket in the filed table`}</title>
                  </rect>
                ) : (
                  <rect x={bx} y={y(c.total)} width={band.bandwidth} height={Math.max(1, y(0) - y(c.total))} className="fill-accent">
                    <title>{`${c.label}: ${formatCompact(c.total)} ${cur}`}</title>
                  </rect>
                )}
                <text x={cx} y={y(c.total) - 5} textAnchor="middle" fontSize={11} className="fill-text num font-semibold">
                  {formatCompact(c.total)}
                </text>
                <text x={cx} y={H - 24} textAnchor="middle" fontSize={11} className={AXIS_TEXT}>
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
      {schedule.leases.length > 0 ? (
        <section aria-labelledby="leases-heading" className="flex min-w-0 flex-col gap-1">
          <h3 id="leases-heading" className="font-heading text-lg font-semibold">
            Lease facts (not part of the ladder)
          </h3>
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[20rem] text-sm">
              <caption className="sr-only">Filed lease facts</caption>
              <tbody>
                {schedule.leases.map((l) => (
                  <tr key={l.id} className="border-b border-border-soft">
                    <th scope="row" className="px-3 py-1 text-left font-normal">
                      {l.label}
                    </th>
                    <td className="num px-3 py-1 text-right">{formatCompact(l.value)}</td>
                    <td className="num px-3 py-1 text-right text-text-muted">{l.periodEnd}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </ChartFrame>
  );
}
