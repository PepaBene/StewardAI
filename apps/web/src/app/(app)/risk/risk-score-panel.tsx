"use client";

import { useMemo, useState } from "react";

import { LimitedState } from "@/components/insight/limited-state";
import { ResourceGate } from "@/components/company/primitives";
import { HBar } from "@/components/charts/hbar";
import { BandGauge, type GaugeZone } from "@/components/insight/band-gauge";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { VerdictLine } from "@/components/insight/verdict-line";
import { RangeButtons } from "@/components/portfolio/range-buttons";
import { notScored } from "@/components/portfolio/risk-glance";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import type { components } from "@/lib/api/schema";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { indexName } from "@/lib/portfolio/ticker";
import { cn } from "@/lib/utils";

/*
 * Risk score (insight I12): one 0-100 number for how risky the portfolio has been, built from
 * volatility, worst fall, bad-day loss, concentration and market sensitivity, with the market's
 * own score as the marker. Below it each input in plain words, every holding against the
 * portfolio and the market, and which holdings move together.
 */

type Headline = components["schemas"]["RiskHeadline"];
type HoldingRisk = components["schemas"]["HoldingRisk"];

const fetchHeadline = () => getBrowserApiClient().GET("/portfolio/risk/score");

// The bands of `steward.decision.risk_score.SCORE_BANDS`.
const ZONES: readonly GaugeZone[] = [
  { label: "Lower than the market", to: 35, tone: "gain" },
  { label: "About the market", to: 55, tone: "neutral" },
  { label: "Riskier", to: 75, tone: "neutral" },
  { label: "Much riskier", to: 100, tone: "loss" },
];

const METRICS = [
  { id: "volatility", label: "Volatility" },
  { id: "max_drawdown", label: "Worst fall" },
  { id: "beta", label: "Market sensitivity" },
] as const;
type Metric = (typeof METRICS)[number]["id"];

function fmt(metric: string, v: number): string {
  return metric === "beta" ? v.toFixed(2) : formatPercent(v, { decimals: 1 });
}

function Tiles({ data }: { data: Headline }) {
  return (
    <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {data.tiles.map((t) => (
        <div key={t.key} className="border-t border-border-soft pt-2">
          <dt className="text-xs text-text-muted">{t.label}</dt>
          <dd className="num text-lg font-semibold">
            {t.value === null || t.value === undefined ? "—" : fmt(t.unit === "ratio" ? "beta" : t.key, t.value)}
            {t.benchmark_value !== null && t.benchmark_value !== undefined ? (
              <span className="ml-2 text-xs font-normal text-text-muted">market {fmt(t.unit === "ratio" ? "beta" : t.key, t.benchmark_value)}</span>
            ) : null}
          </dd>
          {t.verdict ? (
            <VerdictLine verdict={t.verdict.tone} className="mt-1 text-xs">
              {t.verdict.phrase}
            </VerdictLine>
          ) : t.reason ? (
            <p className="mt-1 text-xs text-text-muted">{t.reason}</p>
          ) : null}
        </div>
      ))}
    </dl>
  );
}

function PerHolding({ data }: { data: Headline }) {
  const [metric, setMetric] = useState<Metric>("volatility");
  const rows = useMemo(
    () =>
      [...data.holdings]
        .filter((h) => h[metric] !== null && h[metric] !== undefined)
        .sort((a, b) => Math.abs(Number(b[metric])) - Math.abs(Number(a[metric]))),
    [data.holdings, metric],
  );
  const missing = data.holdings.length - rows.length;
  const pRef = data.portfolio_reference[metric];
  const mRef = data.market_reference[metric];
  const values = [...rows.map((r) => Math.abs(Number(r[metric]))), Math.abs(pRef ?? 0), Math.abs(mRef ?? 0)];
  const max = Math.max(...values, 0.01);
  const bar = (label: string, v: number, strong = false) => (
    <li key={label} className="grid grid-cols-[minmax(7rem,12rem)_1fr_4.5rem] items-center gap-3 text-sm">
      <span className={cn(strong && "font-semibold")}>{label}</span>
      <HBar value={Math.abs(v)} min={0} max={max} tone={strong ? "muted" : undefined} label={`${label} ${fmt(metric, v)}`} formatValue={(x) => fmt(metric, x)} />
      <span className="num text-right">{fmt(metric, v)}</span>
    </li>
  );
  const table = (
    <table className="w-full text-sm">
      <caption className="sr-only">Risk per holding</caption>
      <thead>
        <tr className="bg-surface-muted text-left text-xs text-text-muted">
          <th scope="col" className="px-3 py-1.5 font-medium">Holding</th>
          <th scope="col" className="px-3 py-1.5 text-right font-medium">Weight</th>
          <th scope="col" className="px-3 py-1.5 text-right font-medium">Volatility</th>
          <th scope="col" className="px-3 py-1.5 text-right font-medium">Worst fall</th>
          <th scope="col" className="px-3 py-1.5 text-right font-medium">Sensitivity</th>
          <th scope="col" className="px-3 py-1.5 text-right font-medium">Days</th>
        </tr>
      </thead>
      <tbody>
        {data.holdings.map((h: HoldingRisk) => (
          <tr key={h.ticker} className="border-t border-border-soft">
            <td className="px-3 py-1">{h.ticker}</td>
            <td className="num px-3 py-1 text-right">{formatPercent(h.weight, { decimals: 1 })}</td>
            <td className="num px-3 py-1 text-right">{h.volatility === null || h.volatility === undefined ? "—" : fmt("volatility", h.volatility)}</td>
            <td className="num px-3 py-1 text-right">{h.max_drawdown === null || h.max_drawdown === undefined ? "—" : fmt("max_drawdown", h.max_drawdown)}</td>
            <td className="num px-3 py-1 text-right">{h.beta === null || h.beta === undefined ? "—" : fmt("beta", h.beta)}</td>
            <td className="num px-3 py-1 text-right">{h.observations}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker="Each holding against the portfolio and the market"
        title="Where the risk sits"
        action={<RangeButtons options={METRICS} value={metric} onChange={setMetric} label="Measure" />}
      />
      <ChartWithFooter
        className="mt-3"
        chart={
          <ul className="space-y-1.5">
            {pRef !== null && pRef !== undefined ? bar("Your portfolio", pRef, true) : null}
            {mRef !== null && mRef !== undefined ? bar(data.benchmark_symbol ? `Market (${indexName(data.benchmark_symbol)})` : "Market", mRef, true) : null}
            {rows.map((r) => bar(r.ticker, Number(r[metric])))}
          </ul>
        }
        table={table}
        dataset="Daily returns over the window"
        asOf={data.window_end ? formatDate(data.window_end) : null}
        source="Stored daily closes"
        learnHref="/methods/risk_score"
      />
      {missing > 0 ? <p className="mt-2 text-xs text-text-muted">{missing} holding{missing === 1 ? "" : "s"} without enough price history for this measure.</p> : null}
    </Blueprint>
  );
}

function corrClass(v: number): string {
  if (v >= 0.6) return "bg-diverging-over text-accent-foreground";
  if (v >= 0.2) return "bg-diverging-over-subtle text-text";
  if (v > -0.2) return "bg-diverging-neutral text-text";
  if (v > -0.6) return "bg-diverging-under-subtle text-text";
  return "bg-diverging-under text-accent-foreground";
}

function Correlation({ data }: { data: Headline }) {
  const m = data.correlation;
  if (m.tickers.length < 2) return null;
  if (m.pairs_measured === 0) {
    return (
      <LimitedState>
        Which holdings move together needs {m.min_observations} shared trading days per pair; none of the {m.pairs_total} pairs has that yet.
      </LimitedState>
    );
  }
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker={m.average === null || m.average === undefined ? "Not enough overlapping days" : `Average ${m.average.toFixed(2)} over ${m.pairs_measured} of ${m.pairs_total} pairs`}
        title="Which holdings move together"
      />
      <div className="mt-3 grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="overflow-x-auto">
          <table className="border-separate border-spacing-0.5 text-xs">
            <caption className="sr-only">Correlation of daily returns between each pair of holdings (lower triangle)</caption>
            <thead>
              <tr>
                <td />
                {m.tickers.slice(0, -1).map((t) => (
                  <th key={t} scope="col" className="h-20 w-9 align-bottom font-medium text-text-muted">
                    <span className="inline-block origin-bottom-left translate-x-3 -rotate-60 whitespace-nowrap">{t.split("_")[0]}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {m.tickers.slice(1).map((row, ri) => {
                const i = ri + 1;
                return (
                  <tr key={row}>
                    <th scope="row" className="pr-2 text-right font-medium whitespace-nowrap text-text-muted">{row.split("_")[0]}</th>
                    {m.tickers.slice(0, -1).map((col, j) => {
                      if (j >= i) return <td key={col} />;
                      const v = m.values[i]?.[j];
                      return (
                        <td
                          key={col}
                          title={v === null || v === undefined ? `${row} and ${col}: fewer than ${m.min_observations} shared days` : `${row} and ${col}: ${v.toFixed(2)}`}
                          className={cn("num h-8 w-9 text-center", v === null || v === undefined ? "text-text-faint" : corrClass(v))}
                        >
                          {v === null || v === undefined ? "·" : v.toFixed(1)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-text-muted" aria-hidden="true">
            <span className="bg-diverging-under px-1.5 text-accent-foreground">−1 opposite</span>
            <span className="bg-diverging-neutral px-1.5 text-text">0 unrelated</span>
            <span className="bg-diverging-over px-1.5 text-accent-foreground">+1 together</span>
          </p>
        </div>
        <div className="space-y-4 text-sm">
          <div>
            <h4 className="text-xs font-medium text-text-muted">Move most together</h4>
            <ul className="mt-1 divide-y divide-border-soft">
              {data.together.map((p) => (
                <li key={`${p.a}-${p.b}`} className="flex justify-between py-1">
                  <span>{p.a.split("_")[0]} and {p.b.split("_")[0]}</span>
                  <span className="num">{p.correlation.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="text-xs font-medium text-text-muted">Move least together</h4>
            <ul className="mt-1 divide-y divide-border-soft">
              {data.least_together.map((p) => (
                <li key={`${p.a}-${p.b}`} className="flex justify-between py-1">
                  <span>{p.a.split("_")[0]} and {p.b.split("_")[0]}</span>
                  <span className="num">{p.correlation.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          </div>
          <p className="text-xs text-text-muted">Pairs need {m.min_observations} shared trading days; a dot means fewer.</p>
        </div>
      </div>
    </Blueprint>
  );
}

function Body({ data }: { data: Headline }) {
  const s = data.score;
  const markers = s.benchmark_score === null || s.benchmark_score === undefined ? [] : [{ value: s.benchmark_score, label: data.benchmark_symbol ? `Market (${indexName(data.benchmark_symbol)})` : "Market" }];
  return (
    <div className="flex flex-col gap-5">
      <Blueprint className="px-[22px] py-4">
        <PanelHeading
          kicker={data.window_start && data.window_end ? `${formatDate(data.window_start)} to ${formatDate(data.window_end)}, ${data.observations} trading days` : "Risk score"}
          title={s.score === null || s.score === undefined ? "Risk score not available" : `Risk score ${Math.round(s.score)}: ${s.band ?? ""}`}
        />
        {s.status === "unknown" ? (
          <LimitedState className="mt-3">{notScored(s.reason)}</LimitedState>
        ) : (
          <div className="mt-3 max-w-xl">
            <BandGauge value={s.score} min={0} max={100} zones={ZONES} marker="Your portfolio" markers={markers} format={(v) => Math.round(v).toString()} label="Risk score from 0 (no risk) to 100 (very high)" />
          </div>
        )}
        <Tiles data={data} />
        <details className="mt-4">
          <summary className="cursor-pointer text-sm text-accent-ink">How the score is built</summary>
          <table className="mt-2 w-full text-sm">
            <caption className="sr-only">Score inputs, their reference levels and weights</caption>
            <thead>
              <tr className="bg-surface-muted text-left text-xs text-text-muted">
                <th scope="col" className="px-3 py-1.5 font-medium">Input</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Value</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Scores 50 at</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Scores 100 at</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Sub-score</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Weight</th>
              </tr>
            </thead>
            <tbody>
              {s.sub_scores.map((x) => (
                <tr key={x.input} className="border-t border-border-soft">
                  <td className="px-3 py-1">{x.label}{x.reason ? <span className="block text-xs text-text-muted">{x.reason}</span> : null}</td>
                  <td className="num px-3 py-1 text-right">{x.value === null || x.value === undefined ? "—" : fmt(x.input, x.value)}</td>
                  <td className="num px-3 py-1 text-right">{fmt(x.input, x.reference)}<span className="block text-xs text-text-muted">{x.reference_basis === "benchmark" ? "the market" : "fixed level"}</span></td>
                  <td className="num px-3 py-1 text-right">{fmt(x.input, x.very_high)}</td>
                  <td className="num px-3 py-1 text-right">{x.score === null || x.score === undefined ? "—" : Math.round(x.score)}</td>
                  <td className="num px-3 py-1 text-right">{formatPercent(x.weight, { decimals: 0 })}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 font-mono text-xs text-text-muted">{s.formula}</p>
          <p className="mt-1 text-xs text-text-muted">{data.concentration_basis} Method {s.method_version}.</p>
          {data.warnings.length > 0 ? <p className="mt-1 text-xs text-text-muted">Read with the score: {data.warnings.join(" ")}</p> : null}
        </details>
      </Blueprint>
      <PerHolding data={data} />
      <Correlation data={data} />
    </div>
  );
}

export function RiskScorePanel() {
  const { state } = useApiResource(fetchHeadline);
  return (
    <ResourceGate state={state} label="the risk score">
      {(data: Headline) => <Body data={data} />}
    </ResourceGate>
  );
}
