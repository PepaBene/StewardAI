"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { ResourceGate } from "@/components/company/primitives";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { DotStrip, priceVsFair, type StripDot, type StripRef } from "@/components/insight/dot-strip";
import { GroupedTable, type GroupColumn } from "@/components/insight/grouped-table";
import { KpiStrip, type KpiTileProps } from "@/components/insight/kpi-tile";
import { LimitedState } from "@/components/insight/limited-state";
import { StepLineChart, StepSparkline } from "@/components/insight/step-line";
import { ChartFrame } from "@/components/portfolio/chart-frame";
import { fetchValuation, ValuationBenchmarkPanel } from "@/components/portfolio/valuation-benchmark-panel";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { shortTicker } from "@/lib/portfolio/ticker";
import {
  coverageVerdict,
  overvaluedWeightVerdict,
  staleVerdict,
  valueGapVerdict,
} from "@/lib/verdicts/portfolio";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/charts/time";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { money, pct } from "@/lib/insight/income";
import { num, stepValues } from "@/lib/insight/insight";
import { groupHoldings, historyRows, type Basis, type HoldingFairValue, type PortfolioFairValue } from "@/lib/insight/valuation";
import { cn } from "@/lib/utils";

import { ConsensusCoverage } from "./consensus-coverage";

/** The price-vs-fair-value history draws only from this many stored days (plan P17). */
const HISTORY_MIN_DAYS = 30;

const fetchPrices =() => getBrowserApiClient().GET("/portfolio/holdings/prices", { params: { query: { days: 90 } } });

export function PortfolioValuationView() {
  const [basis, setBasis] = useState<Basis>("dcf_default");
  const fetcher = useCallback(() => getBrowserApiClient().GET("/portfolio/fair-value", { params: { query: { basis } } }), [basis]);
  const { state } = useApiResource(fetcher);
  return (
    <ResourceGate state={state} label="portfolio valuation">
      {(v: PortfolioFairValue) => <Body v={v} basis={basis} onBasis={setBasis} />}
    </ResourceGate>
  );
}

function BasisSwitch({ v, basis, onBasis }: { v: PortfolioFairValue; basis: Basis; onBasis: (b: Basis) => void }) {
  return (
    <div role="radiogroup" aria-label="Fair-value basis" className="flex flex-wrap gap-1">
      {Object.entries(v.bases).map(([k, label]) => (
        <button
          key={k}
          type="button"
          role="radio"
          aria-checked={basis === k}
          onClick={() => onBasis(k as Basis)}
          className={cn("border px-2.5 py-1 text-xs", basis === k ? "border-accent bg-accent-soft text-accent-ink" : "border-border text-text-muted hover:text-text")}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function PremiumChip({ upside }: { upside: number | null }) {
  if (upside === null) return <span className="text-text-faint">—</span>;
  const over = upside < 0;
  return (
    <span className={cn("num inline-block border px-1.5 text-xs", over ? "border-loss text-loss" : "border-gain text-gain")}>
      {over ? `${pct(-upside, 0)} above` : `${pct(upside, 0)} below`}
    </span>
  );
}

function Body({ v, basis, onBasis }: { v: PortfolioFairValue; basis: Basis; onBasis: (b: Basis) => void }) {
  const ccy = v.account_currency;
  const priceState = useApiResource(fetchPrices).state;
  const prices = new Map(priceState.kind === "ok" ? priceState.data.holdings.map((p) => [p.ticker, p.bars] as const) : []);
  const groups = groupHoldings(v.holdings);
  const benchmark = useApiResource(fetchValuation).state;
  const diff = num(v.difference);
  const covV = num(v.covered_value);
  const fairV = num(v.fair_value);
  const portV = num(v.portfolio_value);
  const gapVerdict = valueGapVerdict(diff);
  const overW = num(v.overvalued_weight);
  const overVerdict = overvaluedWeightVerdict(v.covered > 0 ? overW : null);
  const coverVerdict = coverageVerdict(v.covered, v.total);
  const staleV = staleVerdict(v.stale_count);
  const tiles: KpiTileProps[] = [
    {
      label: "Holdings with a fair value",
      value: `${v.covered} of ${v.total}`,
      note: `${v.basis_label}${covV !== null ? ` · ${money(covV, ccy, 0)} of value covered` : ""}`,
      verdict: coverVerdict,
    },
    {
      label: "Fair value against market",
      value: diff === null ? "—" : pct(diff, 1, true),
      signed: diff === null ? undefined : diff >= 0 ? "gain" : "loss",
      note: fairV !== null ? `Fair value ${money(fairV, ccy, 0)}` : undefined,
      verdict: gapVerdict,
    },
    {
      label: "Overvalued weight",
      value: v.covered > 0 ? pct(overW, 0) : "—",
      note: `Undervalued ${pct(v.undervalued_weight, 0)}${portV !== null ? ` · portfolio ${money(portV, ccy, 0)}` : ""}`,
      verdict: overVerdict,
    },
    { label: "Stale fair values", value: v.stale_count, note: "Inputs older than the Checks limit", verdict: staleV },
  ];
  const stripDots: StripDot[] = v.holdings.map((h) => {
    const value = num(h.value_account);
    return {
      key: h.ticker,
      label: h.name ?? shortTicker(h.ticker),
      value: priceVsFair(num(h.price), num(h.fair_value)),
      weight: value !== null && portV ? value / portV : null,
    };
  });
  const stripRefs: StripRef[] =
    covV !== null && fairV !== null && fairV > 0 && v.covered > 0 ? [{ label: "Covered holdings together", value: covV / fairV - 1, dashed: true }] : [];
  const columns: GroupColumn<HoldingFairValue>[] = [
    {
      key: "name",
      header: "Holding",
      cell: (h) => (
        <Link href={`/companies/${encodeURIComponent(h.ticker)}/valuation`} className="hover:underline">
          {h.name ?? h.ticker}
        </Link>
      ),
    },
    { key: "ret", header: "Return", align: "right", wide: true, cell: (h) => pct(h.return_pct, 1, true) },
    {
      key: "price",
      header: "Price",
      align: "right",
      cell: (h) => (
        <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
          {money(h.price, h.price_currency)} <PremiumChip upside={num(h.upside)} />
        </span>
      ),
    },
    {
      key: "fair",
      header: "Fair value",
      align: "right",
      cell: (h) =>
        num(h.fair_value) === null ? (
          <span className="text-xs text-text-faint">{h.reason ?? "none"}</span>
        ) : (
          <span>
            {money(h.fair_value, h.price_currency)}
            <span className="block text-xs text-text-muted">
              {h.basis_label}
              {h.scenario_version ? ` v${h.scenario_version}` : ""}
            </span>
          </span>
        ),
    },
    {
      key: "age",
      header: "Age",
      align: "right",
      wide: true,
      cell: (h) => (h.age_days === null ? "—" : <span title={h.stale_reasons.join("; ") || undefined}>{h.age_days} d</span>),
    },
    {
      key: "spark",
      header: "90 days, price and fair value",
      wide: true,
      cell: (h) => {
        const pts = prices.get(h.ticker) ?? [];
        const dates = pts.map((p) => p.date);
        const fair = stepValues(dates, h.history);
        return <StepSparkline rows={pts.map((p, i) => ({ date: p.date, value: p.close, fair: fair[i] }))} label={`${h.name ?? h.ticker}: price and ${h.basis_label}`} />;
      },
    },
    { key: "event", header: "Last event", wide: true, cell: (h) => <span className="text-xs text-text-muted">{h.last_event ?? "—"}</span> },
  ];
  return (
    <div className="flex flex-col gap-5">
      <BasisSwitch v={v} basis={basis} onBasis={onBasis} />
      <KpiStrip label="Valuation at a glance" tiles={tiles} />
      <ChartFrame
        title="Price against fair value, holding by holding"
        kicker={`${v.basis_label} · covered holdings only`}
        reading="Left of zero is cheaper than fair value, right is dearer. Bigger dots are larger holdings."
        dataset={`${v.covered} of ${v.total} holdings, price against the ${v.basis_label} fair value`}
        asOf={v.as_of ? formatDay(v.as_of) : null}
        source="Latest closes, stored fair values"
        learnHref="/glossary"
      >
        <DotStrip
          dots={stripDots}
          refs={stripRefs}
          convention="price-vs-fair"
          sizeByWeight
          format={(n) => pct(n, 0, true)}
          label={`Price against fair value, ${v.basis_label}`}
        />
      </ChartFrame>
      {v.history.length < HISTORY_MIN_DAYS ? (
        <LimitedState
          need={{ min: HISTORY_MIN_DAYS, have: v.history.length, unit: "stored days" }}
          since={v.history_starts ? `Recording since ${formatDay(v.history_starts)}` : undefined}
        >
          The history of market value against fair value draws once enough days are stored.
        </LimitedState>
      ) : (
      <Blueprint className="px-[22px] py-4">
        <PanelHeading kicker={`${ccy ?? "Account currency"} · covered holdings only`} title="Market value against fair value" />
        <ChartWithFooter
          className="mt-3"
          chart={
            <StepLineChart
              rows={historyRows(v)}
              valueLabel="Market value"
              fairLabel={`Fair value (${v.basis_label})`}
              format={(n) => money(n, ccy, 0)}
              label="Covered market value and recorded fair value by day"
              today={v.as_of ?? undefined}
            />
          }
          table={
            <table className="w-full text-sm">
              <caption className="sr-only">Daily covered value and fair value</caption>
              <thead>
                <tr className="bg-surface-muted text-xs text-text-muted">
                  <th scope="col" className="px-3 py-1.5 text-left font-medium">Date</th>
                  <th scope="col" className="px-3 py-1.5 text-right font-medium">Market value</th>
                  <th scope="col" className="px-3 py-1.5 text-right font-medium">Fair value</th>
                </tr>
              </thead>
              <tbody>
                {[...v.history].reverse().map((r) => (
                  <tr key={r.date} className="border-t border-border-soft">
                    <td className="num px-3 py-1">{formatDay(r.date)}</td>
                    <td className="num px-3 py-1 text-right">{money(r.covered_value, ccy, 0)}</td>
                    <td className="num px-3 py-1 text-right">{money(r.fair_value, ccy, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          }
          dataset={v.history_starts ? `Fair values recorded daily from ${formatDay(v.history_starts)}` : "No fair value recorded yet"}
          asOf={v.as_of ? formatDay(v.as_of) : null}
          source="fair_value_point, position snapshots"
          learnHref="/glossary"
        />
      </Blueprint>
      )}
      <GroupedTable
        caption="Holdings grouped by price against fair value"
        rowKey={(h) => h.ticker}
        columns={columns}
        groups={[
          { key: "over", title: "Overvalued", tone: "loss", explanation: "Price above the fair value on this basis.", rows: groups.over },
          { key: "stale", title: "Stale", tone: "warning", explanation: "The fair value's inputs are too old to trust; refresh the scenario before reading a verdict.", rows: groups.stale },
          { key: "under", title: "Undervalued and fair", tone: "gain", explanation: "Price at or below the fair value on this basis.", rows: groups.under },
        ]}
      />
      {groups.uncovered.length > 0 ? (
        <details className="blueprint px-4 py-3">
          <summary className="cursor-pointer text-sm">
            <span className="font-semibold">No fair value</span> <span className="num text-text-muted">{groups.uncovered.length}</span>
            <span className="ml-2 text-xs text-text-muted">Not on this basis; left out of the totals. Choose another basis or add a scenario.</span>
          </summary>
          <ul className="mt-3 divide-y divide-border-soft">
            {groups.uncovered.map((h) => (
              <li key={h.ticker} className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-sm">
                <Link href={`/companies/${encodeURIComponent(h.ticker)}`} className="hover:underline">
                  {h.name ?? shortTicker(h.ticker)}
                </Link>
                <span className="flex items-center gap-3 text-xs text-text-muted">
                  {h.reason ? <span>{h.reason}</span> : null}
                  <Link href={`/companies/${encodeURIComponent(h.ticker)}/valuation`} className="font-medium text-accent-ink hover:underline">
                    Add a scenario
                  </Link>
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <ValuationBenchmarkPanel state={benchmark} />
      <p className="text-xs text-text-faint">
        Method {v.method_version}. {v.note}
      </p>
      <ConsensusCoverage />
    </div>
  );
}
