"use client";

import { ArrowDownIcon, ArrowUpDownIcon, ArrowUpIcon, LoaderCircleIcon, RefreshCwIcon } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { AlertMessage } from "@/components/alert-message";
import { EmptyState } from "@/components/empty-state";
import { DriftStatusBadge } from "@/components/portfolio/drift-status-badge";
import { FreshnessLabel } from "@/components/portfolio/freshness-label";
import { Button } from "@/components/ui/button";
import { getBrowserApiClient, type HoldingsResponse } from "@/lib/api/client";
import {
  formatMoney,
  formatPercent,
  formatPp,
  formatPrice,
  formatQuantity,
  gainLossTone,
} from "@/lib/format";
import { useApiResource } from "@/lib/hooks/use-api-resource";
import {
  DEFAULT_HOLDINGS_SORT,
  nextSort,
  sortHoldings,
  type HoldingsSort,
  type HoldingsSortKey,
} from "@/lib/portfolio/holdings-sort";
import { cn } from "@/lib/utils";

const fetchHoldings = () => getBrowserApiClient().GET("/portfolio/holdings");

const TONE_CLASS = { gain: "text-gain", loss: "text-loss", flat: "" };

export function HoldingsView() {
  const { state, reload } = useApiResource(fetchHoldings);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Holdings</h1>
          <p className="mt-1 text-sm text-text-muted">
            Every open Trading212 position. Open one for its thesis, verdicts and evidence.
          </p>
        </div>
        {state.kind === "ok" && state.data.freshness ? (
          <FreshnessLabel
            syncedAt={state.data.freshness.synced_at}
            snapshotDate={state.data.freshness.snapshot_date}
          />
        ) : null}
      </div>

      {state.kind === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-text-muted" role="status">
          <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin" />
          Loading holdings…
        </p>
      ) : null}

      {state.kind === "error" ? (
        <AlertMessage title={state.title}>
          <p>{state.detail}</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={() => void reload()}>
            <RefreshCwIcon aria-hidden="true" />
            Try again
          </Button>
        </AlertMessage>
      ) : null}

      {state.kind === "ok" ? (
        <HoldingsTable data={state.data} />
      ) : state.kind === "error" && state.previous ? (
        <HoldingsTable data={state.previous} />
      ) : null}
    </div>
  );
}

const COLUMNS: { key: HoldingsSortKey | null; label: string; numeric: boolean }[] = [
  { key: "name", label: "Holding", numeric: false },
  { key: null, label: "Quantity", numeric: true },
  { key: null, label: "Avg price", numeric: true },
  { key: null, label: "Price", numeric: true },
  { key: "value", label: "Value", numeric: true },
  { key: "weight", label: "Weight", numeric: true },
  { key: "drift", label: "Drift", numeric: true },
  { key: "ppl", label: "Unrealised P/L", numeric: true },
];

function HoldingsTable({ data }: { data: HoldingsResponse }) {
  const [sort, setSort] = useState<HoldingsSort>(DEFAULT_HOLDINGS_SORT);
  const rows = useMemo(() => sortHoldings(data.holdings, sort), [data.holdings, sort]);

  if (!data.freshness) {
    return (
      <EmptyState title="No holdings yet">
        The first Trading212 sync lists your positions here. Use &ldquo;Sync now&rdquo; in the
        header to run one.
      </EmptyState>
    );
  }
  if (rows.length === 0) {
    return (
      <EmptyState title="No open positions">
        Trading212 reported no open positions in the latest sync.
      </EmptyState>
    );
  }

  const baseCcy = data.base_currency ?? "";
  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-surface-raised">
      <table className="w-full min-w-[56rem] text-sm">
        <caption className="sr-only">
          Open positions, sorted by {sort.key} {sort.direction === "asc" ? "ascending" : "descending"}
        </caption>
        <thead className="bg-surface-muted">
          <tr className="border-b border-border text-text-muted">
            {COLUMNS.map((column) => {
              const active = column.key !== null && sort.key === column.key;
              const ariaSort = active ? (sort.direction === "asc" ? "ascending" : "descending") : undefined;
              const SortIcon = !active ? ArrowUpDownIcon : sort.direction === "asc" ? ArrowUpIcon : ArrowDownIcon;
              return (
                <th
                  key={column.label}
                  scope="col"
                  aria-sort={ariaSort}
                  className={cn("px-3 py-2 font-medium", column.numeric ? "text-right" : "text-left")}
                >
                  {column.key ? (
                    <button
                      type="button"
                      onClick={() => setSort((s) => nextSort(s, column.key!))}
                      className={cn(
                        "inline-flex items-center gap-1 rounded-sm hover:text-text",
                        active && "text-text",
                      )}
                    >
                      {column.label}
                      {column.key === "value" ? ` (${baseCcy})` : null}
                      <SortIcon aria-hidden="true" className="size-3.5" />
                    </button>
                  ) : (
                    column.label
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((h) => {
            const tone = gainLossTone(h.ppl);
            return (
              <tr key={h.ticker} className="border-b border-border last:border-0">
                <th scope="row" className="px-3 py-2 text-left font-normal">
                  <Link
                    href={`/holdings/${encodeURIComponent(h.ticker)}`}
                    className="group block rounded-sm"
                  >
                    <span className="block font-medium text-text group-hover:text-accent group-hover:underline">
                      {h.name ?? h.ticker}
                    </span>
                    <span className="block font-mono text-xs text-text-muted">{h.ticker}</span>
                  </Link>
                </th>
                <td className="num px-3 py-2 text-right">{formatQuantity(h.quantity)}</td>
                <td className="num px-3 py-2 text-right text-text-muted">
                  {formatPrice(h.avg_price, h.instrument_currency)}
                </td>
                <td className="num px-3 py-2 text-right">{formatPrice(h.current_price, h.instrument_currency)}</td>
                <td className="num px-3 py-2 text-right">
                  {formatMoney(h.value_base, baseCcy)}
                  {h.account_currency !== baseCcy || h.instrument_currency !== baseCcy ? (
                    <span className="block text-xs text-text-muted" title="Trading212's own conversion">
                      T212 {formatMoney(h.value_account, h.account_currency)}
                    </span>
                  ) : null}
                </td>
                <td className="num px-3 py-2 text-right">
                  {formatPercent(h.weight)}
                  {h.target_weight !== null ? (
                    <span className="block text-xs text-text-muted">target {formatPercent(h.target_weight)}</span>
                  ) : null}
                </td>
                <td className="px-3 py-2 text-right">
                  {h.drift_status === "no_target" ? (
                    <DriftStatusBadge status="no_target" />
                  ) : (
                    <span className="inline-flex flex-col items-end gap-1">
                      <span className="num">{formatPp(h.drift)}</span>
                      <DriftStatusBadge status={h.drift_status} />
                    </span>
                  )}
                </td>
                <td className={cn("num px-3 py-2 text-right", TONE_CLASS[tone])}>
                  {formatMoney(h.ppl, h.account_currency, { signed: true })}
                  <span className="block text-xs">{formatPercent(h.ppl_pct, { signed: true })}</span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
