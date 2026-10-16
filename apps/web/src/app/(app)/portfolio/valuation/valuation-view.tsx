"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ResourceGate } from "@/components/company/primitives";
import { AnswerStrip, CoverageChip } from "@/components/insight/answer-strip";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { GroupedTable, type GroupColumn } from "@/components/insight/grouped-table";
import { StepLineChart, StepSparkline } from "@/components/insight/step-line";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDay } from "@/lib/charts/time";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { money, pct } from "@/lib/insight/income";
import { num, stepValues } from "@/lib/insight/insight";
import { groupHoldings, historyRows, type Basis, type HoldingFairValue, type PortfolioFairValue } from "@/lib/insight/valuation";
import { cn } from "@/lib/utils";

import { ConsensusCoverage } from "./consensus-coverage";

const fetchPrices = () => getBrowserApiClient().GET("/portfolio/holdings/prices", { params: { query: { days: 90 } } });

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
  const diff = num(v.difference);
  const tone = v.status === "complete" ? "neutral" : v.status === "partial" ? "partial" : "unavailable";
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
            <span className="block text-[11px] text-text-muted">
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
      <AnswerStrip
        lead={{
          label: `Fair value of covered holdings, ${v.basis_label}`,
          value: v.fair_value === null ? "Not available" : money(v.fair_value, ccy, 0),
          sentence:
            diff === null
              ? "No holding has a fair value on this basis yet."
              : `${pct(Math.abs(diff), 1)} ${diff >= 0 ? "above" : "below"} what the market prices the same ${v.covered} holdings at (${money(v.covered_value, ccy, 0)}). Holdings without a fair value are left out, not counted at zero.`,
        }}
        chip={<CoverageChip tone={tone}>{`${v.covered} of ${v.total} holdings covered`}</CoverageChip>}
        figures={[
          { label: "Portfolio value", value: money(v.portfolio_value, ccy, 0) },
          { label: "Upside in money", value: money(v.upside_amount, ccy, 0) },
          { label: "Overvalued weight", value: pct(v.overvalued_weight, 0), note: `Undervalued ${pct(v.undervalued_weight, 0)}` },
          { label: "Stale fair values", value: v.stale_count, note: "Inputs older than the Checks limit" },
        ]}
      />
      {v.uncovered.length ? (
        <AlertMessage tone="info" title="Not covered on this basis">
          {v.uncovered.join(", ")}. Choose another basis or{" "}
          <Link href="/config/checks" className="text-accent-ink underline underline-offset-2">
            check the staleness rules
          </Link>
          .
        </AlertMessage>
      ) : null}
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
      <GroupedTable
        caption="Holdings grouped by price against fair value"
        rowKey={(h) => h.ticker}
        columns={columns}
        groups={[
          { key: "over", title: "Overvalued", tone: "loss", explanation: "Price above the fair value on this basis.", rows: groups.over },
          { key: "stale", title: "Stale", tone: "warning", explanation: "The fair value's inputs are too old to trust; refresh the scenario before reading a verdict.", rows: groups.stale },
          { key: "under", title: "Undervalued and fair", tone: "gain", explanation: "Price at or below the fair value on this basis.", rows: groups.under },
          { key: "none", title: "No fair value", tone: "neutral", explanation: "Not on this basis; left out of the totals.", rows: groups.uncovered },
        ]}
      />
      <p className="text-xs text-text-faint">
        Method {v.method_version}. {v.note}
      </p>
      <ConsensusCoverage />
    </div>
  );
}
