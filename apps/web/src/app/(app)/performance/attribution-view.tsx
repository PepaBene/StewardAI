"use client";

import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { WaterfallChart, type WaterfallStep } from "@/components/charts/waterfall";
import { ChartWithFooter } from "@/components/insight/chart-footer";
import { formatCompact } from "@/lib/charts/format";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { RangeButtons } from "@/components/portfolio/range-buttons";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { CsvDownload } from "@/components/ui/csv-download";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent, gainLossTone } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { fetchExport } from "@/lib/performance/export";
import { barScale, type AttributionData } from "@/lib/performance/performance";
import { cn } from "@/lib/utils";

import { Formula, Money, PriceAge, TD, TH, TH_LEFT, Toned } from "./shared";

const RANGES = [
  { id: "30", label: "1M" },
  { id: "90", label: "3M" },
  { id: "365", label: "1Y" },
  { id: "1095", label: "3Y" },
] as const;
type Range = (typeof RANGES)[number]["id"];

const COMPONENT_LABEL: Record<string, string> = {
  price: "Prices moved",
  fx: "Exchange rates moved",
  income: "Dividends received",
  fees: "Fees and taxes paid",
};

function Bars({ rows, currency }: { rows: { label: string; value: number; note?: string }[]; currency: string | null }) {
  const scale = useMemo(() => barScale(rows.map((r) => r.value)), [rows]);
  return (
    <ul className="space-y-2">
      {rows.map((r) => {
        const bar = scale(r.value);
        const tone = gainLossTone(r.value);
        return (
          <li key={r.label} className="grid grid-cols-[minmax(9rem,14rem)_1fr_8rem] items-center gap-3 text-sm">
            <span>
              {r.label}
              {r.note ? <span className="block text-[11px] text-text-muted">{r.note}</span> : null}
            </span>
            <span className="relative block h-3 bg-border-soft" aria-hidden="true">
              <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
              <span
                className={cn("absolute inset-y-0", tone === "loss" ? "bg-loss" : tone === "gain" ? "bg-gain" : "bg-text-faint")}
                style={{ left: `${bar.offset * 100}%`, width: `${bar.width * 100}%` }}
              />
            </span>
            <span className="text-right"><Money value={r.value} currency={currency} signed decimals={2} /></span>
          </li>
        );
      })}
    </ul>
  );
}

export function AttributionView() {
  const [range, setRange] = useState<Range>("365");
  const fetcher = useMemo(
    () => () => getBrowserApiClient().GET("/portfolio/performance/attribution", { params: { query: { days: Number(range) } } }),
    [range],
  );
  const { state } = useApiResource(fetcher);
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="max-w-2xl text-sm text-text-muted">
          Where the gain over the window came from, in your base currency. Moving money in or out of the account is not a gain and is left out.
        </p>
        <RangeButtons options={RANGES} value={range} onChange={setRange} label="Window" />
      </div>
      <ResourceGate state={state} label="contribution">
        {(data: AttributionData) => <Body data={data} />}
      </ResourceGate>
    </div>
  );
}

function Body({ data }: { data: AttributionData }) {
  const [dimension, setDimension] = useState<string>("currency");
  if (!data.bridge) {
    return (
      <AlertMessage tone="info" title="No contribution to show yet">
        {data.gap ?? "A window needs at least two daily snapshots."}
      </AlertMessage>
    );
  }
  const b = data.bridge;
  const currency = data.base_currency;
  const rows = [
    ...Object.entries(b.components).map(([key, value]) => ({ label: COMPONENT_LABEL[key] ?? key, value })),
    { label: "Not assigned", value: b.residual, note: "interest, cash translation and broker rounding the stored data cannot split" },
  ];
  const fx = b.components.fx ?? 0;
  const income = b.components.income ?? 0;
  const fees = b.components.fees ?? 0;
  const steps: WaterfallStep[] = [
    { id: "unrealised", label: "Unrealised", value: b.price_unrealised, kind: "delta" },
    { id: "realised", label: "Realised", value: b.price_realised, kind: "delta" },
    { id: "income", label: "Dividends", value: income, kind: "delta" },
    { id: "fx", label: "Currency", value: fx, kind: "delta" },
    { id: "fees", label: "Fees", value: fees, kind: "delta" },
    { id: "residual", label: "Not assigned", value: b.residual, kind: "delta", derived: true },
    { id: "total", label: "Total", value: b.price_unrealised + b.price_realised + income + fx + fees + b.residual, kind: "total" },
  ];
  const groupNames = Object.keys(data.groups);
  const active = groupNames.includes(dimension) ? dimension : groupNames[0];
  const group = active ? Object.entries(data.groups[active]).sort((a, c) => c[1] - a[1]) : [];
  return (
    <div className="flex flex-col gap-5">
      <div>
        <PriceAge freshness={data.freshness} benchmark={!!data.benchmark} />
      </div>
      {b.unattributed_tickers.length > 0 ? (
        <AlertMessage title="Part of the portfolio could not be attributed">
          The number of units changed without a stored fill for {b.unattributed_tickers.join(", ")} (a split, or a missing transaction). Their moves are left in “Not assigned”.
        </AlertMessage>
      ) : null}
      <Blueprint className="px-[22px] py-4">
        <PanelHeading
          kicker={data.start && data.end ? `${formatDate(data.start)} to ${formatDate(data.end)}` : undefined}
          title={
            <>
              Your portfolio {b.gain.value !== null && b.gain.value !== undefined && b.gain.value < 0 ? "lost" : "gained"}{" "}
              <Money value={b.gain.value === null || b.gain.value === undefined ? null : Math.abs(b.gain.value)} currency={currency} />
            </>
          }
        />
        <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <div><dt className="text-xs text-text-muted">Value at the start</dt><dd className="num text-lg font-semibold"><Money value={b.start_value} currency={currency} /></dd></div>
          <div><dt className="text-xs text-text-muted">Money you added or took out</dt><dd className="num text-lg font-semibold"><Money value={b.net_external_flows} currency={currency} signed /></dd></div>
          <div><dt className="text-xs text-text-muted">Value now</dt><dd className="num text-lg font-semibold"><Money value={b.end_value} currency={currency} /></dd></div>
          <div><dt className="text-xs text-text-muted">Bought minus sold</dt><dd className="num text-lg font-semibold"><Money value={b.net_trading} currency={currency} signed /></dd></div>
        </dl>
        <ChartWithFooter
          className="mt-5"
          chart={
            <WaterfallChart
              steps={steps}
              width={640}
              height={220}
              label={`Where the gain came from, ${currency ?? "base currency"}`}
              formatValue={(v) => formatCompact(v)}
            />
          }
          table={<Bars rows={rows} currency={currency} />}
          dataset="Daily position snapshots and fills"
          asOf={data.end ? formatDate(data.end) : null}
          source="Trading212 account history"
          learnHref="/methods/realised_unrealised"
        />
        <p className="mt-3 text-xs text-text-muted">
          The parts add up to the gain. Realised is the price gain on units sold in the window; unrealised is the price move on units still held. “Not assigned” (dashed) is the balancing figure. “Bought minus sold” is money moved between cash and holdings, so it is not a gain or a loss.
          {b.gain.coverage !== null && b.gain.coverage !== undefined && b.gain.coverage < 1
            ? ` ${formatPercent(b.gain.coverage, { decimals: 0 })} of the portfolio's value is attributed.`
            : ""}
        </p>
      </Blueprint>

      <Blueprint className="px-[22px] py-4">
        <PanelHeading
          kicker="Biggest movers first"
          title="By holding"
          action={<CsvDownload label="Contribution CSV" filename={`performance-attribution-${data.days}d.csv`} fetchCsv={() => fetchExport("attribution", data.days)} />}
        />
        <div className="mt-3 overflow-x-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Contribution of each holding over the window</caption>
            <thead>
              <tr className="bg-surface-muted">
                <th scope="col" className={TH_LEFT}>Holding</th>
                <th scope="col" className={TH}>Price</th>
                <th scope="col" className={TH}>Of which realised</th>
                <th scope="col" className={TH}>Exchange rate</th>
                <th scope="col" className={TH}>Dividends</th>
                <th scope="col" className={TH}>Fees</th>
                <th scope="col" className={TH}>Total</th>
                <th scope="col" className={TH}>Of starting value</th>
                {data.benchmark ? <th scope="col" className={TH}>Against the benchmark</th> : null}
              </tr>
            </thead>
            <tbody>
              {data.holdings.map((h) => (
                <tr key={h.ticker} className="border-t border-border-soft">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {h.name ?? h.ticker}
                    {h.name ? <span className="block text-[11px] text-text-muted">{h.ticker}</span> : null}
                  </th>
                  {h.attributed ? (
                    <>
                      <td className={TD}><Money value={h.price} currency={currency} signed decimals={2} /></td>
                      <td className={TD}>{h.realised ? <Money value={h.realised} currency={currency} signed decimals={2} /> : <span className="text-text-muted">—</span>}</td>
                      <td className={TD}><Money value={h.fx} currency={currency} signed decimals={2} /></td>
                      <td className={TD}><Money value={h.income} currency={currency} signed decimals={2} /></td>
                      <td className={TD}><Money value={h.fees} currency={currency} signed decimals={2} /></td>
                      <td className={cn(TD, "font-semibold")}><Money value={h.total} currency={currency} signed decimals={2} /></td>
                      <td className={TD}>{h.pct_of_start === null || h.pct_of_start === undefined ? "—" : <Toned value={h.pct_of_start}>{formatPercent(h.pct_of_start, { signed: true, decimals: 2 })}</Toned>}</td>
                      {data.benchmark ? (
                        <td className={TD}>
                          {h.active === null || h.active === undefined ? (
                            <span className="text-xs text-text-muted">{h.active_reason ?? "—"}</span>
                          ) : (
                            <Money value={h.active} currency={currency} signed decimals={2} />
                          )}
                        </td>
                      ) : null}
                    </>
                  ) : (
                    <td colSpan={data.benchmark ? 8 : 7} className="px-3 py-1.5 text-right"><Absent reason="missing: units changed without a stored fill" /></td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.benchmark ? (
          <p className="mt-3 max-w-3xl text-xs text-text-muted">
            “Against the benchmark” is a holding&apos;s total minus what its value at the start would have gained in {data.benchmark.label} ({formatPercent(data.benchmark.benchmark_return, { signed: true, decimals: 2 })} over the same dates). A holding bought during the window has no starting value to compare.
            {data.benchmark.like_for_like ? "" : ` Not a like-for-like comparison: ${data.benchmark.notes.join("; ") || "the benchmark's currency or dividend basis is not the same as yours"}.`}
          </p>
        ) : null}
      </Blueprint>

      {groupNames.length > 0 ? (
        <Blueprint className="px-[22px] py-4">
          <PanelHeading
            kicker="Holdings without a label are shown as Unknown"
            title="By group"
            action={
              <RangeButtons
                options={groupNames.map((n) => ({ id: n, label: n }))}
                value={active}
                onChange={setDimension}
                label="Group by"
              />
            }
          />
          <div className="mt-4">
            <Bars rows={group.map(([label, value]) => ({ label, value }))} currency={currency} />
          </div>
        </Blueprint>
      ) : null}
      <Formula lines={data.explain.formula} version={data.explain.formula_version} />
    </div>
  );
}
