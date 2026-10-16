import { ChartEmpty, Radar } from "@/components/charts";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { formatNumber, formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { fundProvenance, type LookThroughReport } from "@/lib/portfolio/look-through";
import type { RiskFigures } from "@/lib/portfolio/performance";
import { benchmarkValues, riskScaleNote, type RiskAxis } from "@/lib/portfolio/risk-profile";

/**
 * Risk profile: a radar over the axes the data supports (trailing-year volatility, beta and
 * drawdown of the flow-adjusted index; concentration and foreign-currency share — with the
 * ETFs looked through when their holdings are stored), each on a stated fixed scale, plus
 * the raw figures. The benchmark is drawn as a dashed outline through the axes where it has
 * a value (volatility and drawdown from its price history, beta 1 by definition,
 * concentration and FX from its constituents' proxy fund).
 */
export function RiskPanel({
  figures,
  axes,
  fxShare,
  benchmarkSymbol,
  loading,
  lookThrough,
}: {
  figures: RiskFigures | null;
  axes: RiskAxis[];
  fxShare: number | null;
  benchmarkSymbol: string | null;
  loading: boolean;
  lookThrough?: LookThroughReport | null;
}) {
  const bench = benchmarkSymbol ?? "benchmark";
  const lt = lookThrough?.available ? lookThrough : null;
  const ltBench = lt?.benchmark ?? null;
  const conc = lt?.concentration ?? null;
  const fx = lt?.fx ?? null;
  const shownFx = fx?.foreign_share ?? fxShare;
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
    {
      k: fx?.foreign_share != null ? "FX share · look-through" : "FX share",
      v: shownFx !== null ? formatPct(shownFx, 0) : "—",
      note:
        fx?.benchmark_foreign_share != null && ltBench
          ? `${ltBench.label} ${formatPct(fx.benchmark_foreign_share, 0)}`
          : shownFx !== null
            ? "non-base currency"
            : undefined,
    },
  ];
  if (conc) {
    stats.push({
      k: "Top 10 · look-through",
      v: formatPct(conc.top10_share, 0),
      note: `eff. ${formatNumber(conc.effective_n, 0)} names${ltBench?.concentration ? ` · ${ltBench.label} ${formatPct(ltBench.concentration.top10_share, 0)}` : ""}`,
    });
  }
  const window = figures ? (figures.fullYear ? "trailing year" : `since ${formatDay(figures.from)}`) : null;
  const benchValues = benchmarkValues(axes);
  const benchLabel = ltBench ? `${ltBench.label} (${bench})` : bench;
  const proxyFund = ltBench?.symbol ? lt?.funds.find((f) => f.benchmark_proxy_for.includes(ltBench.symbol!)) : undefined;

  return (
    <Blueprint className="flex min-w-0 flex-col gap-2.5 px-6 py-5.5" aria-label="Risk profile">
      <PanelHeading kicker="Risk profile" title="Portfolio risk, normalised" />
      {axes.length >= 3 ? (
        <Radar
          axes={axes.map((a) => a.label)}
          series={[
            { label: "Portfolio", values: axes.map((a) => a.portfolio) },
            ...(benchValues ? [{ label: benchLabel, values: benchValues, dashed: true, partial: true }] : []),
          ]}
          label="Risk profile, each axis 0 to 100% of its stated scale"
          formatValue={(v) => formatPct(v, 0)}
        />
      ) : (
        <ChartEmpty minHeight={200} label={loading ? "Loading history…" : "Not enough history for a risk profile yet"} />
      )}
      <dl className="grid grid-cols-2 gap-y-3 border-t border-border pt-3 sm:grid-cols-3">
        {stats.map((s) => (
          <div key={s.k} className="min-w-0">
            <dt className="truncate text-xs text-text-muted">{s.k}</dt>
            <dd className="num font-heading text-xl font-semibold">{s.v}</dd>
            {s.note ? <dd className="truncate text-xs text-text-faint" title={s.note}>{s.note}</dd> : null}
          </div>
        ))}
      </dl>
      <p className="text-xs leading-snug text-text-faint">
        {window ? `Volatility, drawdown and beta over the ${window} of the flow-adjusted index; beta in local currencies. ` : null}
        {lt
          ? `Concentration and FX with the ETFs looked through (${formatPct(lt.coverage?.portfolio_share_looked_through ?? 0, 0)} of value covered). `
          : null}
        {benchValues ? `Dashed: ${benchLabel}` : null}
        {benchValues && ltBench && proxyFund
          ? `, concentration and FX proxied by ${proxyFund.label}'s holdings (${fundProvenance(proxyFund)})`
          : null}
        {benchValues ? "; axes with no benchmark value are left open. " : null}
        {riskScaleNote(axes)}
        {axes.some((a) => a.key === "valuation") ? null : " No valuation axis: nothing here measures it."}
      </p>
    </Blueprint>
  );
}
