import type { TransactionType } from "@/lib/api/client";

/*
 * Filters of the transactions view, kept in the page URL (so a filtered period can be
 * linked and reloaded) and passed to GET /portfolio/transactions unchanged. Dates are
 * UTC days, both ends inclusive, as the API reads them.
 */

export const TRANSACTION_TYPES = ["order", "dividend", "cash"] as const satisfies readonly TransactionType[];
export const TYPE_LABELS: Record<TransactionType, string> = {
  order: "Orders",
  dividend: "Dividends",
  cash: "Cash movements",
};

/** One row's type. */
export const TYPE_SINGULAR: Record<TransactionType, string> = { order: "Order", dividend: "Dividend", cash: "Cash" };

export type TransactionFilters = {
  types: TransactionType[];
  ticker: string | null;
  from: string | null;
  to: string | null;
};

export const EMPTY_TRANSACTION_FILTERS: TransactionFilters = { types: [], ticker: null, from: null, to: null };

const TICKER = /^[A-Za-z0-9._-]{1,32}$/;

/** A real calendar date as YYYY-MM-DD, or null. */
export function isoDate(value: string | null | undefined): string | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return null;
  const d = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === value ? value : null;
}

export function isTicker(value: string): boolean {
  return TICKER.test(value);
}

export function parseTransactionFilters(params: URLSearchParams): TransactionFilters {
  const ticker = params.get("ticker");
  return {
    types: TRANSACTION_TYPES.filter((t) => params.getAll("type").includes(t)),
    ticker: ticker && TICKER.test(ticker) ? ticker : null,
    from: isoDate(params.get("from")),
    to: isoDate(params.get("to")),
  };
}

export function transactionFiltersToSearch(filters: TransactionFilters): string {
  const params = new URLSearchParams();
  TRANSACTION_TYPES.filter((t) => filters.types.includes(t)).forEach((t) => params.append("type", t));
  if (filters.ticker) params.set("ticker", filters.ticker);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  const text = params.toString();
  return text ? `?${text}` : "";
}

/** The query for the API: empty filters are left out. */
export function transactionQuery(filters: TransactionFilters, cursor: string | null, limit: number) {
  return {
    type: filters.types.length ? filters.types : undefined,
    ticker: filters.ticker ?? undefined,
    from: filters.from ?? undefined,
    to: filters.to ?? undefined,
    cursor: cursor ?? undefined,
    limit,
  };
}

/** Rows already shown plus a new page, each id once (in case pages overlap). */
export function appendUnique<T extends { id: string }>(existing: T[], page: T[]): T[] {
  const seen = new Set(existing.map((r) => r.id));
  const out = [...existing];
  for (const row of page) {
    if (!seen.has(row.id)) {
      seen.add(row.id);
      out.push(row);
    }
  }
  return out;
}

export type PeriodPreset = { id: string; label: string; from: string | null; to: string | null };

/** Quick periods relative to `today` (UTC). */
export function periodPresets(today: Date = new Date()): PeriodPreset[] {
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const y = today.getUTCFullYear();
  const yearAgo = new Date(today);
  yearAgo.setUTCFullYear(y - 1);
  yearAgo.setUTCDate(yearAgo.getUTCDate() + 1);
  return [
    { id: "ytd", label: "This year", from: `${y}-01-01`, to: iso(today) },
    { id: "last-year", label: String(y - 1), from: `${y - 1}-01-01`, to: `${y - 1}-12-31` },
    { id: "12m", label: "Last 12 months", from: iso(yearAgo), to: iso(today) },
    { id: "all", label: "All time", from: null, to: null },
  ];
}
