"use client";

import { Sankey } from "@/components/charts";
import {
  getBrowserApiClient,
  type Holding,
  type TotalByCurrency,
  type TransactionOut,
  type TransactionType,
} from "@/lib/api/client";
import type { ApiResult } from "@/lib/hooks/use-api-resource";
import type { CashFlowGraph } from "@/lib/portfolio/cash-flow";
import { formatCompactMoney } from "@/lib/portfolio/money";
import { collectPages } from "@/lib/portfolio/transactions";

export type CashFlowData = {
  /** Totals over every transaction in the period (all types). */
  totals: TotalByCurrency[];
  /** Rows of the requested types/subtypes in the period, paged in full when `rowsComplete`. */
  rows: TransactionOut[];
  rowsComplete: boolean;
  holdings: Holding[];
  /** Account currency (from the holdings list); null before the first sync. */
  currency: string | null;
};

const PAGE = 500;
const MAX_PAGES = 10;

/**
 * Loads a period's cash flow: the totals (one request, `limit=1`), the rows needed for a
 * split (paged through the cursor, at most MAX_PAGES × 500), and the holdings list for
 * security types — a fixed number of requests, never one per holding.
 */
export async function fetchCashFlow(
  from: string | null,
  to: string | null,
  rowFilter: { type?: TransactionType[]; subtype?: string[] },
): Promise<ApiResult<CashFlowData>> {
  const api = getBrowserApiClient();
  const range = { from: from ?? undefined, to: to ?? undefined };
  const [totals, holdings] = await Promise.all([
    api.GET("/portfolio/transactions", { params: { query: { ...range, limit: 1 } } }),
    api.GET("/portfolio/holdings"),
  ]);
  if (!totals.data) return { error: totals.error, response: totals.response };
  let failed: ApiResult<CashFlowData> | null = null;
  const { items, complete } = await collectPages<TransactionOut>(async (cursor) => {
    const r = await api.GET("/portfolio/transactions", {
      params: { query: { ...range, ...rowFilter, cursor: cursor ?? undefined, limit: PAGE } },
    });
    if (!r.data) {
      failed = { error: r.error, response: r.response };
      return { items: [], next_cursor: null };
    }
    return r.data;
  }, MAX_PAGES);
  if (failed) return failed;
  return {
    data: {
      totals: totals.data.totals,
      rows: items,
      rowsComplete: complete,
      holdings: holdings.data?.holdings ?? [],
      currency: holdings.data?.account_currency ?? totals.data.totals[0]?.currency ?? null,
    },
    response: totals.response,
  };
}

/** The cash-flow sankey with compact money labels; the accessible table carries every flow. */
export function CashFlowSankey({
  graph,
  currency,
  label,
  height = 270,
}: {
  graph: CashFlowGraph;
  currency: string;
  label: string;
  height?: number;
}) {
  return (
    <Sankey
      nodes={graph.nodes}
      links={graph.links}
      height={height}
      labelWidth={100}
      formatValue={(v) => formatCompactMoney(v, currency)}
      label={label}
    />
  );
}
