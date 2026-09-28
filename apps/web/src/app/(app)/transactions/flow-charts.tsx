"use client";

import { useCallback, useMemo, useState } from "react";

import { ChartEmpty, SrTable, StackedBar } from "@/components/charts";
import { CashFlowSankey, fetchCashFlow } from "@/components/portfolio/cash-flow-sankey";
import { RangeButtons } from "@/components/portfolio/range-buttons";
import { Blueprint, PanelHeading } from "@/components/ui/blueprint";
import { getBrowserApiClient, type TotalByCurrency } from "@/lib/api/client";
import { MONTH_SHORT } from "@/lib/charts/time";
import { formatMoney } from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  cashFlowGraph,
  dividendsByTicker,
  dividendTotal,
  flowPeriods,
  monthlyOrderFlows,
  splitBuys,
  yearEarlier,
  type FlowPeriod,
  type MonthFlow,
} from "@/lib/portfolio/cash-flow";
import { formatCompactMoney, formatSignedPct } from "@/lib/portfolio/money";
import { shortTicker } from "@/lib/portfolio/ticker";
import { cn } from "@/lib/utils";

const fetchOverview = () => getBrowserApiClient().GET("/portfolio/overview");

/**
 * The charts above the table: the period's cash flow as a sankey, bought vs sold per month,
 * and dividend income by payer. They have their own period switch (this quarter, YTD, 1Y)
 * and cover every transaction in it, whatever the table below is filtered to.
 */
export function FlowCharts() {
  const periods = useMemo(() => flowPeriods(), []);
  const [periodId, setPeriodId] = useState<FlowPeriod["id"]>("ytd");
  const period = periods.find((p) => p.id === periodId)!;

  const fetcher = useCallback(() => fetchCashFlow(period.from, period.to, { type: ["order", "dividend"] }), [period.from, period.to]);
  const { state } = useApiResource(fetcher);
  const prevFetcher = useCallback(() => {
    const prev = yearEarlier(period);
    return getBrowserApiClient().GET("/portfolio/transactions", {
      params: { query: { type: ["dividend"], from: prev.from, to: prev.to, limit: 1 } },
    });
  }, [period]);
  const prev = useApiResource(prevFetcher).state;
  const overview = useApiResource(fetchOverview).state;

  const data = state.kind === "ok" ? state.data : state.kind === "error" ? state.previous : null;
  const currency = data?.currency ?? null;
  const graph = data && currency ? cashFlowGraph(data.totals, currency, data.rowsComplete ? splitBuys(data.rows, data.holdings, currency) : null) : null;
  const months = data && currency && data.rowsComplete ? monthlyOrderFlows(data.rows, currency, period.from, period.to) : null;
  const prevTotals: TotalByCurrency[] | null = prev.kind === "ok" ? prev.data.totals : null;
  const cost = overview.kind === "ok" ? overview.data.account : null;

  const loadingLabel = state.kind === "loading" ? "Loading transactions…" : null;
  const errorLabel = state.kind === "error" && !data ? `Could not load transactions: ${state.title}` : null;

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
      <Blueprint className="flex min-w-0 flex-col gap-3 px-5.5 py-5" aria-label="Cash flow">
        <PanelHeading
          kicker={`Cash flow · ${period.title}`}
          title={
            graph && currency && graph.links.length
              ? `${formatCompactMoney(graph.inflow, currency)} in, ${formatCompactMoney(graph.bought, currency)} put to work`
              : "Where the money went"
          }
          action={
            <RangeButtons
              options={periods.map((p) => ({ id: p.id, label: p.label }))}
              value={periodId}
              onChange={setPeriodId}
              label="Chart period"
            />
          }
        />
        {graph && currency && graph.links.length ? (
          <>
            <CashFlowSankey graph={graph} currency={currency} height={310} label={`Cash flow, ${period.title}, ${currency}`} />
            <p className="text-[11px] leading-snug text-text-faint">
              In {currency} as booked by Trading212.{" "}
              {graph.nodes.some((n) => n.id.startsWith("out:buy:")) ? "Buys split by each instrument's security type. " : ""}
              {graph.nodes.some((n) => n.id === "in:earlier") ? "“Earlier cash” is cash held before the period that was spent in it. " : ""}
              {graph.excludedCurrencies.length ? `Rows in ${graph.excludedCurrencies.join(", ")} are not included.` : ""}
            </p>
          </>
        ) : (
          <ChartEmpty minHeight={240} label={loadingLabel ?? errorLabel ?? "No transactions in this period"} />
        )}
      </Blueprint>

      <div className="flex min-w-0 flex-col gap-5">
        <Blueprint className="flex min-w-0 flex-col gap-2.5 px-5.5 py-5" aria-label="Net flow by month">
          <PanelHeading
            kicker="Net flow by month"
            title="Bought vs sold"
            action={
              <div className="flex gap-3 text-[11px] text-text-muted" aria-hidden="true">
                <span className="flex items-center gap-1.5">
                  <span className="size-2.5 bg-accent" />
                  Buys
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="size-2.5 bg-accent-300" />
                  Sells
                </span>
              </div>
            }
          />
          {months && currency ? (
            <MonthBars months={months} currency={currency} />
          ) : (
            <ChartEmpty
              minHeight={150}
              label={loadingLabel ?? errorLabel ?? (data && !data.rowsComplete ? "Too many orders in this period to chart" : "No orders in this period")}
            />
          )}
        </Blueprint>
        <DividendPanel
          period={period}
          rows={data?.rows ?? null}
          totals={data?.totals ?? null}
          prevTotals={prevTotals}
          currency={currency}
          investedCost={cost && cost.account_currency === currency ? cost.invested_cost : null}
          loadingLabel={loadingLabel ?? errorLabel}
        />
      </div>
    </div>
  );
}

const BAR_UP = 95;

/** Buys up from a shared baseline, sells hanging below it, one column per month. */
function MonthBars({ months, currency }: { months: MonthFlow[]; currency: string }) {
  const max = Math.max(1, ...months.map((m) => Math.max(m.buys, m.sells)));
  const any = months.some((m) => m.buys > 0 || m.sells > 0);
  if (!any) return <ChartEmpty minHeight={150} label="No orders in this period" />;
  const label = (m: string) => MONTH_SHORT[Number(m.slice(5, 7)) - 1];
  // One scale for both directions, so a sell bar is comparable with a buy bar.
  const scale = (BAR_UP - 5) / max;
  const down = Math.max(12, Math.ceil(Math.max(...months.map((m) => m.sells)) * scale) + 2);
  return (
    <figure className="m-0">
      <div className="relative grid gap-1.5" style={{ gridTemplateColumns: `repeat(${months.length}, minmax(0, 1fr))` }} aria-hidden="true">
        <div className="absolute inset-x-0 h-px bg-border" style={{ top: BAR_UP }} />
        {months.map((m) => (
          <div
            key={m.month}
            className="flex flex-col items-center"
            title={`${label(m.month)} ${m.month.slice(0, 4)}: bought ${formatMoney(m.buys, currency, { decimals: 0 })}, sold ${formatMoney(m.sells, currency, { decimals: 0 })}`}
          >
            <div className="flex w-full items-end justify-center" style={{ height: BAR_UP }}>
              <div className="w-[70%] bg-accent" style={{ height: `${m.buys * scale}px` }} />
            </div>
            <div className="flex w-full justify-center" style={{ height: down }}>
              <div className="w-[70%] bg-accent-300" style={{ height: `${m.sells * scale}px` }} />
            </div>
            <span className="font-mono text-[10px] text-text-faint">{label(m.month)}</span>
          </div>
        ))}
      </div>
      <SrTable
        caption={`Bought and sold per month, ${currency}`}
        columns={["Month", "Bought", "Sold"]}
        rows={months.map((m) => [
          `${label(m.month)} ${m.month.slice(0, 4)}`,
          formatMoney(m.buys, currency, { decimals: 0 }),
          formatMoney(m.sells, currency, { decimals: 0 }),
        ])}
      />
    </figure>
  );
}

function DividendPanel({
  period,
  rows,
  totals,
  prevTotals,
  currency,
  investedCost,
  loadingLabel,
}: {
  period: FlowPeriod;
  rows: Parameters<typeof dividendsByTicker>[0] | null;
  totals: TotalByCurrency[] | null;
  prevTotals: TotalByCurrency[] | null;
  currency: string | null;
  investedCost: number | null;
  loadingLabel: string | null;
}) {
  const total = totals && currency ? dividendTotal(totals, currency) : null;
  const prevTotal = prevTotals && currency ? dividendTotal(prevTotals, currency) : null;
  const yoy = total !== null && prevTotal !== null && prevTotal > 0 ? total / prevTotal - 1 : null;
  const payers = rows && currency ? dividendsByTicker(rows, currency, 5) : [];
  const sum = payers.reduce((s, p) => s + p.amount, 0);
  const yieldOnCost = period.id === "1y" && total !== null && investedCost && investedCost > 0 ? total / investedCost : null;

  return (
    <Blueprint className="flex min-w-0 flex-col gap-2.5 px-5.5 py-5" aria-label="Dividend income">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="kicker">Dividend income · {period.title}</div>
          <p className="mt-1 font-heading text-[30px] leading-[1.1] font-semibold">
            {total !== null && currency ? formatMoney(total, currency, { decimals: 0 }) : "—"}{" "}
            {yoy !== null ? (
              <span className={cn("font-sans text-sm font-medium", yoy >= 0 ? "text-gain" : "text-loss")}>
                <span aria-hidden="true">{yoy >= 0 ? "▲" : "▼"}</span> {formatSignedPct(yoy, 0)} y/y
              </span>
            ) : null}
          </p>
        </div>
        {yieldOnCost !== null ? (
          <span className="text-xs text-text-muted" title="Dividends over the last 12 months divided by today's cost basis (Trading212)">
            Yield on cost {formatSignedPct(yieldOnCost).replace("+", "")}
          </span>
        ) : null}
      </div>
      {payers.length && currency ? (
        <StackedBar
          segments={payers.map((p) => ({ label: p.key === "__other" ? p.label : shortTicker(p.label), value: p.amount }))}
          formatShare={(f) => formatCompactMoney(f * sum, currency)}
        />
      ) : (
        <ChartEmpty minHeight={48} label={loadingLabel ?? "No dividends in this period"} />
      )}
      {yoy === null && prevTotal === 0 && total ? <p className="text-[11px] text-text-faint">No dividends in the same period a year earlier.</p> : null}
    </Blueprint>
  );
}
