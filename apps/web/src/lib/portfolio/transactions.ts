import type { TotalByCurrency, TransactionOut, TransactionType } from "@/lib/api/client";

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
  /** Trading212 subtypes (BUY, SELL, DEPOSIT, ...), any of them; empty = every subtype. */
  subtypes: string[];
  ticker: string | null;
  from: string | null;
  to: string | null;
};

export const EMPTY_TRANSACTION_FILTERS: TransactionFilters = { types: [], subtypes: [], ticker: null, from: null, to: null };

const TICKER = /^[A-Za-z0-9._-]{1,32}$/;
const SUBTYPE = /^[A-Za-z_]{1,32}$/;

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
    subtypes: [...new Set(params.getAll("subtype").filter((v) => SUBTYPE.test(v)).map((v) => v.toUpperCase()))],
    ticker: ticker && TICKER.test(ticker) ? ticker : null,
    from: isoDate(params.get("from")),
    to: isoDate(params.get("to")),
  };
}

export function transactionFiltersToSearch(filters: TransactionFilters): string {
  const params = new URLSearchParams();
  TRANSACTION_TYPES.filter((t) => filters.types.includes(t)).forEach((t) => params.append("type", t));
  filters.subtypes.forEach((t) => params.append("subtype", t));
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
    subtype: filters.subtypes.length ? filters.subtypes : undefined,
    ticker: filters.ticker ?? undefined,
    from: filters.from ?? undefined,
    to: filters.to ?? undefined,
    cursor: cursor ?? undefined,
    limit,
  };
}

/** Rows already shown plus a new page, each id once (in case pages overlap). */
export function appendUnique<T extends { id: string | number }>(existing: T[], page: T[]): T[] {
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

/*
 * Quick tabs over the type filter. "Buys" and "Sells" narrow orders by subtype; the type
 * checkboxes stay available for any other combination (then no tab is active).
 */
export const TRANSACTION_TABS = [
  { id: "all", label: "All", types: [], subtypes: [] },
  { id: "buys", label: "Buys", types: ["order"], subtypes: ["BUY"] },
  { id: "sells", label: "Sells", types: ["order"], subtypes: ["SELL"] },
  { id: "dividends", label: "Dividends", types: ["dividend"], subtypes: [] },
  { id: "cash", label: "Cash", types: ["cash"], subtypes: [] },
] as const satisfies readonly { id: string; label: string; types: readonly TransactionType[]; subtypes: readonly string[] }[];
export type TransactionTabId = (typeof TRANSACTION_TABS)[number]["id"];

const sameSet = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((x) => b.includes(x));

/** The tab matching the filters' types and subtypes, or null for another combination. */
export function activeTab(filters: TransactionFilters): TransactionTabId | null {
  return TRANSACTION_TABS.find((t) => sameSet(t.types, filters.types) && sameSet(t.subtypes, filters.subtypes))?.id ?? null;
}

/** The filters with a tab's types and subtypes (ticker and period kept). */
export function withTab(filters: TransactionFilters, id: TransactionTabId): TransactionFilters {
  const tab = TRANSACTION_TABS.find((t) => t.id === id)!;
  return { ...filters, types: [...tab.types], subtypes: [...tab.subtypes] };
}

/** Row counts per tab from totals fetched over every type (same ticker and period). */
export function tabCounts(totals: readonly TotalByCurrency[]): Record<TransactionTabId, number> {
  const count = (pred: (t: TotalByCurrency) => boolean) => totals.filter(pred).reduce((s, t) => s + t.count, 0);
  return {
    all: count(() => true),
    buys: count((t) => t.type === "order" && t.subtype === "BUY"),
    sells: count((t) => t.type === "order" && t.subtype === "SELL"),
    dividends: count((t) => t.type === "dividend"),
    cash: count((t) => t.type === "cash"),
  };
}

type Page<T> = { items: T[]; next_cursor: string | null };

/**
 * Follows the cursor until the last page or `maxPages`. `complete` is false when it stopped
 * early, so a caller never presents a partial sum as the whole.
 */
export async function collectPages<T extends { id: string | number }>(
  fetchPage: (cursor: string | null) => Promise<Page<T>>,
  maxPages = 50,
): Promise<{ items: T[]; complete: boolean }> {
  let items: T[] = [];
  let cursor: string | null = null;
  for (let i = 0; i < maxPages; i++) {
    const page = await fetchPage(cursor);
    items = appendUnique(items, page.items);
    if (!page.next_cursor) return { items, complete: true };
    cursor = page.next_cursor;
  }
  return { items, complete: false };
}

function csvCell(value: string | number | null, text = false): string {
  if (value === null) return "";
  let s = String(value);
  // A text cell starting with a formula character is neutralised (spreadsheet injection).
  if (text && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s;
}

export const CSV_HEADER = ["occurred_at", "type", "subtype", "ticker", "name", "quantity", "price", "amount", "currency", "id"];

/** Rows as CSV (RFC 4180, CRLF line ends), raw values: UTC timestamps, unrounded numbers. */
export function transactionsCsv(rows: readonly TransactionOut[]): string {
  const lines = [CSV_HEADER.join(",")];
  for (const r of rows) {
    lines.push(
      [
        csvCell(r.occurred_at),
        csvCell(r.type),
        csvCell(r.subtype, true),
        csvCell(r.ticker, true),
        csvCell(r.name, true),
        csvCell(r.quantity),
        csvCell(r.price),
        csvCell(r.amount),
        csvCell(r.currency, true),
        csvCell(r.id, true),
      ].join(","),
    );
  }
  return lines.join("\r\n") + "\r\n";
}

/** File name for an export of these filters, e.g. "transactions_2026-01-01_2026-09-27.csv". */
export function csvFileName(filters: TransactionFilters, today: string): string {
  const parts = ["transactions"];
  const tab = activeTab(filters);
  if (tab && tab !== "all") parts.push(tab);
  else if (filters.types.length) parts.push(filters.types.join("-"));
  if (filters.ticker) parts.push(filters.ticker);
  parts.push(filters.from ?? "start", filters.to ?? today);
  return `${parts.join("_")}.csv`;
}
