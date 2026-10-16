import { ChartEmpty, Heatmap, HeatScale, rowsFromMonthlyReturns } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { monthlyReturns } from "@/lib/charts/series";

const CAP = 0.06;

/**
 * Calendar-month returns of the flow-adjusted index (time-weighted), one row per year with
 * the compounded year in the last column. The first month is partial (from the first
 * snapshot); a year's total compounds only the months shown.
 */
export function ReturnsHeatmap({ points, loading }: { points: readonly { date: string; close: number }[]; loading: boolean }) {
  const rows = rowsFromMonthlyReturns(monthlyReturns(points.map((p) => ({ date: p.date, value: p.close }))));
  return (
    <Blueprint className="flex min-w-0 flex-col gap-3.5 px-6 py-5.5" aria-label="Monthly returns">
      <PanelHeading kicker="Returns" title="Monthly heatmap, time-weighted" action={rows.length ? <HeatScale cap={CAP} /> : null} />
      {rows.length ? (
        <div className="relative -mx-6 overflow-x-auto px-6">
          <Heatmap rows={rows} cap={CAP} scale={false} caption="Monthly returns, time-weighted (flow-adjusted)" className="min-w-[36rem]" />
        </div>
      ) : (
        <ChartEmpty minHeight={140} label={loading ? "Loading history…" : "Not enough history for monthly returns yet"} />
      )}
      {rows.length ? (
        <p className="text-xs text-text-faint">
          Each month is its last value over the previous month&rsquo;s; the first month runs from the first snapshot. Deposits and withdrawals
          excluded.
        </p>
      ) : null}
    </Blueprint>
  );
}
