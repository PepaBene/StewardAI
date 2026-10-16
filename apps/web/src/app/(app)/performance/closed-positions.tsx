"use client";

import { ChevronRightIcon } from "lucide-react";
import { useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { ResourceGate } from "@/components/company/primitives";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { CsvDownload } from "@/components/ui/csv-download";
import type { components } from "@/lib/api/schema";
import { getBrowserApiClient } from "@/lib/api/client";
import { formatDate, formatPercent, formatQuantity } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { fetchExport } from "@/lib/performance/export";
import { cn } from "@/lib/utils";

import { Money, TD, TH, TH_LEFT, Toned } from "./shared";

/*
 * Closed positions (insight I11.2): every holding you have sold out of, with what it cost, what
 * the sales brought, the dividends on the way and the money-weighted annual rate. A row opens to
 * the trades behind it, splits included, so each figure can be traced.
 */

type Positions = components["schemas"]["PositionsResponse"];
type Position = components["schemas"]["PositionOut"];

const fetchPositions = () => getBrowserApiClient().GET("/portfolio/performance/positions");

const KIND: Record<Position["transactions"][number]["kind"], string> = {
  buy: "Bought",
  sell: "Sold",
  dividend: "Dividend",
  fee: "Fee or tax",
  split: "Split",
};

function Trades({ p, currency }: { p: Position; currency: string | null }) {
  return (
    <table className="w-full text-xs">
      <caption className="sr-only">Trades behind {p.name ?? p.ticker}</caption>
      <thead>
        <tr className="text-left text-text-muted">
          <th scope="col" className="px-3 py-1 font-medium">Date</th>
          <th scope="col" className="px-3 py-1 font-medium">What</th>
          <th scope="col" className="px-3 py-1 text-right font-medium">Units</th>
          <th scope="col" className="px-3 py-1 text-right font-medium">Price</th>
          <th scope="col" className="px-3 py-1 text-right font-medium">Cash</th>
          <th scope="col" className="px-3 py-1 text-right font-medium">Units after</th>
        </tr>
      </thead>
      <tbody>
        {p.transactions.map((t, i) => (
          <tr key={`${t.day}-${i}`} className="border-t border-border-soft">
            <td className="num px-3 py-1">{formatDate(t.day)}</td>
            <td className="px-3 py-1">
              {KIND[t.kind]}
              {t.note ? <span className="block text-text-muted">{t.note}</span> : null}
            </td>
            <td className="num px-3 py-1 text-right">{t.quantity === null || t.quantity === undefined ? "—" : formatQuantity(t.quantity)}</td>
            <td className="num px-3 py-1 text-right">{t.price === null || t.price === undefined ? "—" : <Money value={t.price} currency={currency} decimals={2} />}</td>
            <td className="num px-3 py-1 text-right"><Money value={t.cash} currency={currency} signed decimals={2} /></td>
            <td className="num px-3 py-1 text-right">{formatQuantity(t.running_quantity)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Row({ p, currency }: { p: Position; currency: string | null }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <tr className="border-t border-border-soft">
        <th scope="row" className="px-3 py-1.5 text-left font-normal">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
            className="inline-flex items-start gap-1 text-left hover:text-accent-ink"
          >
            <ChevronRightIcon aria-hidden="true" className={cn("mt-0.5 size-3.5 shrink-0 transition-transform motion-reduce:transition-none", open && "rotate-90")} />
            <span>
              {p.name ?? p.ticker}
              <span className="block text-[11px] text-text-muted">
                {p.first_trade && p.last_trade ? `${formatDate(p.first_trade)} to ${formatDate(p.last_trade)}` : p.ticker}
              </span>
            </span>
          </button>
        </th>
        <td className={TD}><Money value={p.invested} currency={currency} decimals={2} /></td>
        <td className={TD}><Money value={p.proceeds} currency={currency} decimals={2} /></td>
        <td className={TD}><Money value={p.dividends} currency={currency} decimals={2} /></td>
        <td className={cn(TD, "font-semibold")}><Money value={p.total_gain} currency={currency} signed decimals={2} /></td>
        <td className={TD}>
          {p.annualised_return === null || p.annualised_return === undefined ? (
            <span className="text-xs text-text-muted">{p.annualised_reason ?? "—"}</span>
          ) : (
            <Toned value={p.annualised_return}>{formatPercent(p.annualised_return, { signed: true, decimals: 1 })}</Toned>
          )}
        </td>
        <td className={TD}>{p.days_held === null || p.days_held === undefined ? "—" : `${p.days_held} days`}</td>
      </tr>
      {open ? (
        <tr className="bg-surface-muted/40">
          <td colSpan={7} className="px-2 py-2">
            <Trades p={p} currency={currency} />
            {p.warnings.length > 0 ? <p className="mt-1 px-3 text-xs text-text-muted">{p.warnings.join(" ")}</p> : null}
          </td>
        </tr>
      ) : null}
    </>
  );
}

function Body({ data }: { data: Positions }) {
  if (data.closed.length === 0) {
    return <p className="mt-3 text-sm text-text-muted">No closed positions: you still hold some of everything you have bought.</p>;
  }
  return (
    <>
      <dl className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-3">
        <div><dt className="text-xs text-text-muted">Closed positions</dt><dd className="num text-lg font-semibold">{data.closed.length}</dd></div>
        <div><dt className="text-xs text-text-muted">Realised on them</dt><dd className="num text-lg font-semibold"><Money value={data.closed_realised} currency={data.currency} signed decimals={2} /></dd></div>
        <div><dt className="text-xs text-text-muted">Dividends while held</dt><dd className="num text-lg font-semibold"><Money value={data.closed_dividends} currency={data.currency} decimals={2} /></dd></div>
      </dl>
      <div className="mt-3 overflow-x-auto border border-border">
        <table className="w-full text-sm">
          <caption className="sr-only">Closed positions with their cost, proceeds, dividends and annual rate; open a row for its trades</caption>
          <thead>
            <tr className="bg-surface-muted">
              <th scope="col" className={TH_LEFT}>Holding</th>
              <th scope="col" className={TH}>Invested</th>
              <th scope="col" className={TH}>Sale proceeds</th>
              <th scope="col" className={TH}>Dividends</th>
              <th scope="col" className={TH}>Total gain</th>
              <th scope="col" className={TH}>A year</th>
              <th scope="col" className={TH}>Held</th>
            </tr>
          </thead>
          <tbody>
            {data.closed.map((p) => (
              <Row key={`${p.ticker}-${p.first_trade}`} p={p} currency={data.currency} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 max-w-3xl text-xs text-text-muted">
        “A year” is the money-weighted annual return of the holding&apos;s own buys, sales and dividends; holdings kept under 30 days get none, because a short hold annualises into a misleading number.
      </p>
    </>
  );
}

export function ClosedPositions() {
  const { state } = useApiResource(fetchPositions);
  return (
    <Blueprint className="px-[22px] py-4">
      <PanelHeading
        kicker="Holdings you have sold out of"
        title="Closed positions"
        action={<CsvDownload label="Closed positions CSV" filename="performance-closed-positions.csv" fetchCsv={() => fetchExport("closed_positions")} />}
      />
      <ResourceGate state={state} label="closed positions">
        {(data: Positions) => (
          <>
            {data.warnings.length > 0 ? (
              <div className="mt-3">
                <AlertMessage title="Check these before relying on the figures">{data.warnings.join(" ")}</AlertMessage>
              </div>
            ) : null}
            <Body data={data} />
          </>
        )}
      </ResourceGate>
    </Blueprint>
  );
}
