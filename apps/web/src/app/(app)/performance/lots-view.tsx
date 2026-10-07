"use client";

import { TriangleAlertIcon } from "lucide-react";
import Link from "next/link";

import { AlertMessage } from "@/components/alert-message";
import { Absent, ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { CsvDownload } from "@/components/ui/csv-download";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatMoney, formatQuantity } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { fetchExport } from "@/lib/performance/export";
import { holdingProgress, monthsRemainingLabel, type LotsData } from "@/lib/performance/performance";
import { cn } from "@/lib/utils";

import { Formula, Money, TD, TH, TH_LEFT } from "./shared";

const fetchLots = () => getBrowserApiClient().GET("/portfolio/performance/lots");

function ExemptionMeter({ data }: { data: LotsData }) {
  const year = data.exemption[0];
  if (!year) return null;
  const used = year.limit > 0 ? Math.min(1, year.gross_proceeds / year.limit) : 1;
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading kicker={`${year.year}, sales inside the ${data.time_test_years}-year test`} title="Sale proceeds against your exemption limit" />
      <div className="mt-3 flex flex-wrap items-baseline gap-x-6 gap-y-1">
        <p className="num text-2xl font-semibold">{formatMoney(year.gross_proceeds, year.currency, { decimals: 0 })}</p>
        <p className="text-sm text-text-muted">
          of your {formatMoney(year.limit, year.currency, { decimals: 0 })} limit ·{" "}
          {year.exceeded ? (
            <span className="font-medium text-warning-signal">over by {formatMoney(-year.headroom, year.currency, { decimals: 0 })}</span>
          ) : (
            <span>{formatMoney(year.headroom, year.currency, { decimals: 0 })} left</span>
          )}
        </p>
      </div>
      <div className="mt-2 h-2 max-w-xl bg-border-soft" role="img" aria-label={`${Math.round(used * 100)} percent of the limit used`}>
        <div className={cn("h-full", year.exceeded ? "bg-warning-signal" : "bg-accent")} style={{ width: `${used * 100}%` }} />
      </div>
      {year.sales_without_rate > 0 ? (
        <p className="mt-2 text-xs text-text-muted">{year.sales_without_rate} sale(s) have no stored exchange rate and are not counted, so this is a lower bound.</p>
      ) : null}
      <p className="mt-2 max-w-2xl text-xs text-text-muted">
        Gross sale proceeds from lots still inside the holding period on the day they were sold. The limit and the years are the ones you set under{" "}
        <Link href="/performance/ledger" className="text-accent-ink underline underline-offset-2">Ledger and settings</Link>.
      </p>
    </Blueprint>
  );
}

function LotsTable({ data }: { data: LotsData }) {
  const asOf = data.as_of ?? new Date().toISOString().slice(0, 10);
  if (data.lots.length === 0) return <p className="mt-3 text-sm text-text-muted">No open lots: the stored fills leave no units held.</p>;
  return (
    <div className="mt-3 overflow-x-auto border border-border">
      <table className="w-full text-sm">
        <caption className="sr-only">Open purchase lots and when each passes the holding-period test</caption>
        <thead>
          <tr className="bg-surface-muted">
            <th scope="col" className={TH_LEFT}>Holding</th>
            <th scope="col" className={TH}>Bought</th>
            <th scope="col" className={TH}>Units</th>
            <th scope="col" className={TH}>Cost</th>
            <th scope="col" className={TH_LEFT}>Passes the test</th>
            <th scope="col" className={TH}>Sell target</th>
          </tr>
        </thead>
        <tbody>
          {data.lots.map((lot) => {
            const progress = holdingProgress(lot.bought_on, lot.passes_on, asOf);
            return (
              <tr key={lot.lot_id} className={cn("border-t border-border-soft align-top", lot.forfeits_exemption_warning && "bg-warning-signal-subtle")}>
                <th scope="row" className="px-3 py-1.5 text-left font-normal">
                  {lot.name ?? lot.ticker}
                  {lot.name ? <span className="block text-[11px] text-text-muted">{lot.ticker}</span> : null}
                </th>
                <td className={TD}>{formatDate(lot.bought_on)}</td>
                <td className={TD}>{formatQuantity(lot.quantity)}</td>
                <td className={TD}>{data.currency ? formatMoney(lot.cost, data.currency, { decimals: 0 }) : "—"}</td>
                <td className="px-3 py-1.5">
                  <div className="num text-sm">{formatDate(lot.passes_on)} · {monthsRemainingLabel(lot.months_remaining, lot.passed)}</div>
                  <div className="mt-1 h-1.5 w-40 bg-border-soft" role="img" aria-label={`${Math.round(progress * 100)} percent of the holding period done`}>
                    <div className={cn("h-full", lot.passed ? "bg-gain" : "bg-accent")} style={{ width: `${progress * 100}%` }} />
                  </div>
                  {lot.forfeits_exemption_warning ? (
                    <p className="mt-1 flex items-start gap-1 text-xs font-medium">
                      <TriangleAlertIcon aria-hidden="true" className="mt-px size-3.5 shrink-0 text-warning-signal" />
                      Sell target reached with {monthsRemainingLabel(lot.months_remaining, lot.passed)} to go: selling all of it now would give up the exemption.
                    </p>
                  ) : null}
                </td>
                <td className={TD}>
                  {lot.sell_target === null || lot.sell_target === undefined ? (
                    <Absent reason="missing: no sell target on the thesis" />
                  ) : (
                    <>
                      {formatQuantity(lot.sell_target)}
                      <span className="block text-[11px] text-text-muted">
                        {lot.target_reached === null || lot.target_reached === undefined ? "no price" : lot.target_reached ? "reached" : "not reached"}
                      </span>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Realized({ data }: { data: LotsData }) {
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker={`${data.method === "fifo" ? "Oldest lot first" : "Average cost"}, account currency`}
        title="Sales and realized gain"
        action={<CsvDownload label="Realized gains CSV" filename="performance-realized.csv" fetchCsv={() => fetchExport("realized")} />}
      />
      <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <div><dt className="text-xs text-text-muted">Realized gain</dt><dd className="num text-lg font-semibold"><Money value={data.realized_gain} currency={data.currency} signed decimals={2} /></dd></div>
        <div><dt className="text-xs text-text-muted">Dividends received</dt><dd className="num text-lg font-semibold"><Money value={data.dividends} currency={data.currency} decimals={2} /></dd></div>
        <div><dt className="text-xs text-text-muted">Fees and taxes paid</dt><dd className="num text-lg font-semibold"><Money value={data.fees} currency={data.currency} decimals={2} /></dd></div>
      </dl>
      {data.realized.length > 0 ? (
        <div className="mt-3 max-h-96 overflow-auto border border-border">
          <table className="w-full text-sm">
            <caption className="sr-only">Sales with proceeds, cost and gain</caption>
            <thead>
              <tr className="sticky top-0 bg-surface-muted">
                <th scope="col" className={TH_LEFT}>Sold</th>
                <th scope="col" className={TH_LEFT}>Holding</th>
                <th scope="col" className={TH}>Units</th>
                <th scope="col" className={TH}>Proceeds</th>
                <th scope="col" className={TH}>Cost</th>
                <th scope="col" className={TH}>Gain</th>
                <th scope="col" className={TH}>Held for</th>
              </tr>
            </thead>
            <tbody>
              {data.realized.map((s) => (
                <tr key={s.sale_id} className="border-t border-border-soft">
                  <th scope="row" className="num px-3 py-1 text-left font-normal">{formatDate(s.sold_on)}</th>
                  <td className="px-3 py-1">{s.ticker}</td>
                  <td className={TD}>{formatQuantity(s.quantity)}</td>
                  <td className={TD}><Money value={s.proceeds} currency={data.currency} decimals={2} /></td>
                  <td className={TD}><Money value={s.cost} currency={data.currency} decimals={2} /></td>
                  <td className={TD}><Money value={s.gain} currency={data.currency} signed decimals={2} /></td>
                  <td className={TD}>{s.holding_days === null || s.holding_days === undefined ? "—" : `${s.holding_days} days`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-3 text-sm text-text-muted">No sales in the stored transactions.</p>
      )}
      {data.unmatched.length > 0 ? (
        <div className="mt-3">
          <AlertMessage title="Some sales are larger than the stored history can explain">
            {data.unmatched.map((u) => `${u.ticker} on ${formatDate(u.sold_on)}: ${formatQuantity(u.quantity)} units`).join("; ")}. No gain is given for those units.
          </AlertMessage>
        </div>
      ) : null}
    </Blueprint>
  );
}

export function LotsView() {
  const { state } = useApiResource(fetchLots);
  return (
    <ResourceGate state={state} label="lots">
      {(data: LotsData) =>
        data.status === "unavailable" ? (
          <AlertMessage tone="info" title="No lots to show yet">
            {data.warnings[0] ?? "No transactions are stored."}
          </AlertMessage>
        ) : (
          <div className="flex flex-col gap-5">
            <p className="max-w-3xl text-sm text-text-muted">{data.disclaimer}</p>
            {data.warnings.length > 0 ? (
              <AlertMessage title="Check these before relying on the dates">
                <ul className="list-disc pl-5">
                  {data.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </AlertMessage>
            ) : null}
            <ExemptionMeter data={data} />
            <Blueprint className="px-[22px] py-4">
              <PanelHeading
                kicker={`${data.time_test_years}-year holding period, as of ${data.as_of ? formatDate(data.as_of) : "unknown"}`}
                title="Open lots, soonest first"
                action={<CsvDownload label="Lots CSV" filename="performance-lots.csv" fetchCsv={() => fetchExport("lots")} />}
              />
              <LotsTable data={data} />
            </Blueprint>
            <Realized data={data} />
            <Formula lines={data.explain.formula} version={data.explain.formula_version} />
          </div>
        )
      }
    </ResourceGate>
  );
}
