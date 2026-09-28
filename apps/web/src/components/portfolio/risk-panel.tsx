import { ChartEmpty, Radar } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import type { RiskFigures } from "@/lib/portfolio/performance";
import { RISK_SCALE_NOTE, type RiskAxis } from "@/lib/portfolio/risk-profile";

/**
 * Risk profile: a radar over the axes the data supports (trailing-year volatility, beta and
 * drawdown of the flow-adjusted index; concentration and foreign-currency share of today's
 * weights), each on a stated fixed scale, plus the raw figures. The benchmark is compared in
 * the figures where it has a value; it has no concentration or currency mix, so it is not
 * drawn as a second polygon.
 */
export function RiskPanel({
  figures,
  axes,
  fxShare,
  benchmarkSymbol,
  loading,
}: {
  figures: RiskFigures | null;
  axes: RiskAxis[];
  fxShare: number | null;
  benchmarkSymbol: string | null;
  loading: boolean;
}) {
  const bench = benchmarkSymbol ?? "benchmark";
  const stats: { k: string; v: string; note?: string }[] = [
    {
      k: "Volatility",
      v: figures?.volatility != null ? formatPct(figures.volatility, 1) : "—",
      note: figures?.benchmarkVolatility != null ? `${bench} ${formatPct(figures.benchmarkVolatility, 1)}` : undefined,
    },
    {
      k: "Max DD",
      v: figures?.maxDrawdown != null ? formatPct(figures.maxDrawdown, 1) : "—",
      note: figures?.benchmarkMaxDrawdown != null ? `${bench} ${formatPct(figures.benchmarkMaxDrawdown, 1)}` : undefined,
    },
    { k: "Beta", v: figures?.beta != null ? formatNumber(figures.beta, 2) : "—", note: figures?.beta != null ? `vs ${bench}` : undefined },
    { k: "FX share", v: fxShare !== null ? formatPct(fxShare, 0) : "—", note: fxShare !== null ? "non-base currency" : undefined },
  ];
  const window = figures ? (figures.fullYear ? "trailing year" : `since ${formatDay(figures.from)}`) : null;

  return (
    <Blueprint className="flex min-w-0 flex-col gap-2.5 px-6 py-5.5" aria-label="Risk profile">
      <PanelHeading kicker="Risk profile" title="Portfolio risk, normalised" />
      {axes.length >= 3 ? (
        <Radar
          axes={axes.map((a) => a.label)}
          series={[{ label: "Portfolio", values: axes.map((a) => a.portfolio) }]}
          label="Risk profile, each axis 0 to 100% of its stated scale"
          formatValue={(v) => formatPct(v, 0)}
        />
      ) : (
        <ChartEmpty minHeight={200} label={loading ? "Loading history…" : "Not enough history for a risk profile yet"} />
      )}
      <dl className="grid grid-cols-2 gap-y-3 border-t border-border pt-3 sm:grid-cols-4">
        {stats.map((s) => (
          <div key={s.k} className="min-w-0">
            <dt className="text-[11px] text-text-muted">{s.k}</dt>
            <dd className="num font-heading text-xl font-semibold">{s.v}</dd>
            {s.note ? <dd className="truncate text-[11px] text-text-faint">{s.note}</dd> : null}
          </div>
        ))}
      </dl>
      <p className="text-[11px] leading-snug text-text-faint">
        {window ? `Volatility, drawdown and beta over the ${window} of the flow-adjusted index; beta in local currencies. ` : null}
        {RISK_SCALE_NOTE} No valuation axis: nothing here measures it.
      </p>
    </Blueprint>
  );
}
