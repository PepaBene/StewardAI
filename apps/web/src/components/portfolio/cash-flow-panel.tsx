"use client";

import Link from "next/link";
import { useCallback } from "react";

import { ChartEmpty } from "@/components/charts";
import { CashFlowSankey, fetchCashFlow } from "@/components/portfolio/cash-flow-sankey";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import { cashFlowGraph, splitBuys } from "@/lib/portfolio/cash-flow";
import { formatCompactMoney } from "@/lib/portfolio/money";

/** Year-to-date cash flow on the Overview: where deposits, sales and income went. */
export function CashFlowPanel({ year, today }: { year: number; today: string }) {
  const from = `${year}-01-01`;
  const fetcher = useCallback(() => fetchCashFlow(from, today, { type: ["order"], subtype: ["BUY"] }), [from, today]);
  const { state } = useApiResource(fetcher);
  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const currency = data?.currency ?? null;
  const graph =
    data && currency
      ? cashFlowGraph(data.totals, currency, data.rowsComplete ? splitBuys(data.rows, data.holdings, currency) : null)
      : null;

  return (
    <Blueprint className="flex min-w-0 flex-col gap-3.5 px-6 py-5.5" aria-label="Cash flow year to date">
      <PanelHeading
        kicker={`Cash flow · ${year} YTD`}
        title="Where the money went"
        action={
          <Link href={`/transactions?from=${from}&to=${today}`} className="text-[13px] text-accent-ink hover:underline">
            Transactions →
          </Link>
        }
      />
      {state.kind === "loading" ? (
        <ChartEmpty minHeight={200} label="Loading transactions…" />
      ) : !graph || !currency || graph.links.length === 0 ? (
        <ChartEmpty minHeight={160} label={state.kind === "error" ? `Could not load transactions: ${state.title}` : "No cash movements this year yet"} />
      ) : (
        <>
          <CashFlowSankey graph={graph} currency={currency} label={`Cash flow ${year} year to date, ${currency}`} />
          <p className="text-[11px] leading-snug text-text-faint">
            {formatCompactMoney(graph.inflow, currency)} in, {formatCompactMoney(graph.bought, currency)} bought, in {currency} as booked by
            Trading212.
            {graph.nodes.some((n) => n.id === "out:buy:stock" || n.id === "out:buy:etf")
              ? " Buys split by each instrument's security type."
              : ""}
            {graph.excludedCurrencies.length ? ` Rows in ${graph.excludedCurrencies.join(", ")} are not included.` : ""}
          </p>
        </>
      )}
    </Blueprint>
  );
}
