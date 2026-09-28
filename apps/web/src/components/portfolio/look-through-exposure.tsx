"use client";

import { HBar, StackedBar } from "@/components/charts";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatPct } from "@/lib/charts/format";
import { formatDay } from "@/lib/charts/time";
import { formatMoney } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  DIMENSION_TITLES,
  LOOK_THROUGH_DIMENSIONS,
  dimension,
  exposureSegments,
  fundProvenance,
  heldFunds,
  largestBucket,
  type LookThroughReport,
  type UnderlyingHolding,
} from "@/lib/portfolio/look-through";
import { displayTicker } from "@/lib/portfolio/holdings-view";

const fetchLookThrough = () => getBrowserApiClient().GET("/portfolio/look-through", { params: { query: { top: 25 } } });

export type LookThroughLoad = {
  /** The latest report (kept on screen when a refetch fails). */
  report: LookThroughReport | null;
  loading: boolean;
  /** Why the endpoint could not be read (only when there is no report to show). */
  error: string | null;
};

/**
 * GET /portfolio/look-through, refetched with every successful sync. Look-through is an
 * addition: its absence or failure never blocks a panel, it is only labelled.
 */
export function useLookThrough(): LookThroughLoad {
  const { state } = useApiResource(fetchLookThrough);
  if (state.kind === "ok") return { report: state.data, loading: false, error: null };
  if (state.kind === "error") return { report: state.previous, loading: false, error: state.previous ? null : state.title };
  return { report: null, loading: true, error: null };
}

function viaText(h: UnderlyingHolding): string {
  return h.via.map((v) => `${v.kind === "direct" ? "direct" : v.label} ${formatPct(v.weight, 2)}`).join(" + ");
}

/** The top underlying holdings: direct positions and fund holdings merged by ISIN/issuer. */
export function TopUnderlyingHoldings({
  holdings,
  currency,
  benchmarkLabel,
  limit = 10,
}: {
  holdings: readonly UnderlyingHolding[];
  currency: string;
  benchmarkLabel: string | null;
  limit?: number;
}) {
  const rows = holdings.slice(0, limit);
  if (rows.length === 0) return null;
  const scale = Math.max(...rows.map((h) => h.weight));
  return (
    <div className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold">Top underlying holdings</h3>
      <ol className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Top underlying holdings, direct and through funds">
        {rows.map((h, i) => (
          <li key={h.key} className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-x-2 text-xs">
            <span className="num pt-0.5 text-text-faint">{i + 1}</span>
            <div className="min-w-0">
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate font-medium text-text" title={h.isins.join(", ")}>
                  {h.name}
                  {h.ticker ? <span className="ml-1.5 font-mono text-[10px] text-text-muted">{displayTicker(h.ticker)}</span> : null}
                </span>
              </div>
              <HBar value={h.weight} max={scale} size="sm" label={`${h.name} look-through weight`} className="my-1" />
              <div className="truncate text-[11px] text-text-muted" title={viaText(h)}>
                {viaText(h)}
              </div>
            </div>
            <div className="text-right">
              <div className="num font-semibold">{formatPct(h.weight, 1)}</div>
              <div className="num text-[11px] text-text-muted">{formatMoney(h.value_base, currency, { decimals: 0 })}</div>
              {benchmarkLabel && h.benchmark_weight != null ? (
                <div className="num text-[11px] text-text-faint" title={`${benchmarkLabel} weight`}>
                  {benchmarkLabel} {formatPct(h.benchmark_weight, 1)}
                </div>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** Where the look-through figures come from: one line per held fund, plus coverage. */
export function LookThroughProvenance({ report }: { report: LookThroughReport }) {
  const funds = heldFunds(report);
  const cov = report.coverage;
  return (
    <div className="space-y-1 text-[11px] text-text-muted">
      <ul className="m-0 list-none space-y-0.5 p-0">
        {funds.map((f) => (
          <li key={f.isin}>
            <span className="font-mono text-text">{f.ticker ? displayTicker(f.ticker) : f.label}</span> · {fundProvenance(f)}
            {f.as_of ? <span className="text-text-faint"> · {f.source_label}</span> : null}
            {f.last_attempt && !f.last_attempt.ok && f.last_attempt.error ? (
              <span className="block text-text-faint">Last fetch {formatDay(f.last_attempt.checked_at)}: {f.last_attempt.error}</span>
            ) : null}
          </li>
        ))}
      </ul>
      {cov ? (
        <p className="text-text-faint">
          {formatPct(cov.portfolio_share_looked_through ?? 0, 0)} of value is direct stocks or looked-through funds
          {cov.not_looked_through_value > 0 ? `; ${formatPct(cov.not_looked_through_value / cov.total_value, 0)} in funds not looked through` : ""}
          {cov.cash_value > 0 ? `; cash ${formatPct(cov.cash_value / cov.total_value, 0)}` : ""}. Tier 1: the fund issuers&rsquo; own data, through undocumented endpoints.
        </p>
      ) : null}
    </div>
  );
}

/** FX exposure with the funds expanded, next to each benchmark's currency mix. */
function FxTable({ report }: { report: LookThroughReport }) {
  const fx = report.fx;
  if (!fx || fx.rows.length === 0) return null;
  const others = report.benchmarks.filter((b) => !b.primary);
  const otherWeight = (key: string, i: number) => others[i]?.exposures.currency?.find((b) => b.key === key)?.weight ?? 0;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[20rem] text-xs">
        <caption className="sr-only">Currency exposure against the benchmarks</caption>
        <thead>
          <tr className="border-b border-border text-[10px] tracking-[0.08em] text-text-muted uppercase">
            <th scope="col" className="py-1.5 text-left font-medium">Currency</th>
            <th scope="col" className="py-1.5 text-right font-medium">Portfolio</th>
            {fx.benchmark_label ? <th scope="col" className="py-1.5 text-right font-medium">{fx.benchmark_label}</th> : null}
            {others.map((b) => (
              <th key={b.key} scope="col" className="py-1.5 text-right font-medium">
                {b.label}
                {b.currency_derived ? "*" : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {fx.rows.slice(0, 10).map((r) => (
            <tr key={r.currency} className="border-b border-border-soft">
              <th scope="row" className="py-1 text-left font-mono font-normal">
                {r.currency}
                {r.currency === fx.base_currency ? <span className="ml-1 font-sans text-text-faint">base</span> : null}
              </th>
              <td className="num py-1 text-right">{formatPct(r.weight, 1)}</td>
              {fx.benchmark_label ? <td className="num py-1 text-right text-text-muted">{formatPct(r.benchmark_weight ?? 0, 1)}</td> : null}
              {others.map((b, i) => (
                <td key={b.key} className="num py-1 text-right text-text-muted">
                  {formatPct(otherWeight(r.currency, i), 1)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1 text-[11px] text-text-faint">
        Share of value whose currency is known ({formatPct(fx.known_share, 0)} of the total), cash in its own currency.
        {others.some((b) => b.currency_derived) ? " * Derived from the benchmark's country weights." : ""}
      </p>
    </div>
  );
}

/**
 * Exposure with the ETFs looked through: country, region, sector and currency as 100% bars
 * (cash, the funds' own cash and funds not looked through as their own segments), the
 * largest bucket against the benchmark, where the figures come from, and the top underlying
 * holdings.
 */
export function LookThroughExposure({ report, currency }: { report: LookThroughReport; currency: string }) {
  const bench = report.benchmark;
  return (
    <div className="flex flex-col gap-4.5">
      {LOOK_THROUGH_DIMENSIONS.map((key) => {
        const dim = dimension(report, key);
        if (!dim) return null;
        const largest = largestBucket(dim);
        const benchNote =
          largest && bench && largest.benchmark_weight != null ? ` · ${bench.label} ${formatPct(largest.benchmark_weight, 0)}` : "";
        return (
          <div key={key} title={dim.basis}>
            <StackedBar
              segments={exposureSegments(dim)}
              title={DIMENSION_TITLES[key]}
              note={largest ? `Largest: ${largest.label} ${formatPct(largest.weight, 0)}${benchNote}` : undefined}
            />
          </div>
        );
      })}
      <LookThroughProvenance report={report} />
      <TopUnderlyingHoldings holdings={report.top_holdings} currency={currency} benchmarkLabel={bench?.label ?? null} />
      <details className="text-sm">
        <summary className="cursor-pointer text-xs font-medium text-accent-ink hover:underline">FX exposure against the benchmarks</summary>
        <div className="mt-3">
          <FxTable report={report} />
        </div>
      </details>
      <details className="text-sm">
        <summary className="cursor-pointer text-xs font-medium text-accent-ink hover:underline">How look-through is computed</summary>
        <div className="mt-2 space-y-1.5 text-[11px] leading-snug text-text-muted">
          <p>{report.method}</p>
          {report.exposures.map((d) => (
            <p key={d.dimension}>
              <b className="font-semibold text-text">{DIMENSION_TITLES[d.dimension]}:</b> {d.basis}
            </p>
          ))}
          {report.benchmarks.map((b) => (
            <p key={b.key}>
              <b className="font-semibold text-text">{b.label}:</b> {b.definition}
            </p>
          ))}
          {report.notes.map((n) => (
            <p key={n}>{n}</p>
          ))}
        </div>
      </details>
    </div>
  );
}
