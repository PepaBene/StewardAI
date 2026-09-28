import type { TotalByCurrency, TransactionOut } from "@/lib/api/client";

/*
 * Cash-flow derivations for the Overview and Transactions screens, all from GET
 * /portfolio/transactions: its `totals` (count and sum per type, subtype and currency over
 * every matching row) and, where a split is needed, the rows themselves paged in full.
 * Only one currency is summed at a time (the account currency); other currencies are
 * reported, never converted here.
 */

export type SankeyNode = { id: string; label: string; column: number };
export type SankeyLink = { source: string; target: string; value: number; emphasis?: boolean };

export type BuySplit = { stocks: number; etfs: number; other: number };

export type CashFlowGraph = {
  nodes: SankeyNode[];
  links: SankeyLink[];
  /** Money that came in during the period (deposits, sales, income). */
  inflow: number;
  /** Bought with it. */
  bought: number;
  /** Other currencies present in the totals and left out of the sums. */
  excludedCurrencies: string[];
};

/** "MARKET_BUY" -> "Market buy". */
export function humanizeSubtype(value: string | null): string {
  if (!value) return "Other";
  const words = value.replaceAll("_", " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

const SOURCE_LABEL: Record<string, string> = {
  "cash:DEPOSIT": "Deposits",
  "order:SELL": "Sales",
  "cash:INTEREST": "Interest",
};
const USE_LABEL: Record<string, string> = {
  "cash:WITHDRAW": "Withdrawals",
  "order:BUY": "Buys",
};

/** "ETP", "ETF", "Fund" -> "etf"; any other known type -> "stock"; unknown -> null. */
export function securityClass(securityType: string | null | undefined): "etf" | "stock" | null {
  if (!securityType) return null;
  return /\b(ETF|ETP|ETN|ETC|FUND)\b/i.test(securityType) ? "etf" : "stock";
}

/**
 * The period's cash flow as a three-column graph: sources → "Available" → uses. Deposits,
 * sales, dividends, interest and any other positive flow are sources; buys, withdrawals and
 * any other negative flow are uses. Whatever came in and was not used is "Cash kept"; when
 * more was used than came in, the difference is drawn from "Earlier cash". With `split`,
 * buys are shown as Stocks / ETFs (and "Other buys" for instruments no longer held).
 */
export function cashFlowGraph(
  totals: readonly TotalByCurrency[],
  currency: string,
  split?: BuySplit | null,
): CashFlowGraph {
  const excluded = [...new Set(totals.filter((t) => t.currency !== currency).map((t) => t.currency ?? "unknown"))];
  const sources = new Map<string, { label: string; value: number }>();
  const uses = new Map<string, { label: string; value: number }>();
  const add = (map: typeof sources, key: string, label: string, value: number) => {
    const prev = map.get(key);
    map.set(key, { label, value: (prev?.value ?? 0) + value });
  };

  for (const t of totals) {
    if (t.currency !== currency || t.amount === 0) continue;
    if (t.type === "dividend") {
      if (t.amount > 0) add(sources, "dividend", "Dividends", t.amount);
      else add(uses, "dividend", "Dividend adjustments", -t.amount);
      continue;
    }
    const key = `${t.type}:${t.subtype ?? ""}`;
    if (t.amount > 0) add(sources, key, SOURCE_LABEL[key] ?? humanizeSubtype(t.subtype), t.amount);
    else add(uses, key, USE_LABEL[key] ?? humanizeSubtype(t.subtype), -t.amount);
  }

  const bought = uses.get("order:BUY")?.value ?? 0;
  const splitOk =
    split && bought > 0 && Math.abs(split.stocks + split.etfs + split.other - bought) <= Math.max(1, bought * 1e-6);
  if (splitOk) {
    uses.delete("order:BUY");
    const parts: [string, string, number][] = [
      ["buy:stock", "Stocks", split!.stocks],
      ["buy:etf", "ETFs", split!.etfs],
      ["buy:other", "Other buys", split!.other],
    ];
    const rest = new Map(uses);
    uses.clear();
    for (const [k, label, v] of parts) if (v > 0) uses.set(k, { label, value: v });
    for (const [k, v] of rest) uses.set(k, v);
  }

  const inflow = [...sources.values()].reduce((s, x) => s + x.value, 0);
  const used = [...uses.values()].reduce((s, x) => s + x.value, 0);
  if (used > inflow) sources.set("earlier", { label: "Earlier cash", value: used - inflow });
  else if (inflow > used) uses.set("kept", { label: "Cash kept", value: inflow - used });

  const nodes: SankeyNode[] = [
    ...[...sources].map(([id, s]) => ({ id: `in:${id}`, label: s.label, column: 0 })),
    { id: "available", label: "Available", column: 1 },
    ...[...uses].map(([id, u]) => ({ id: `out:${id}`, label: u.label, column: 2 })),
  ];
  const links: SankeyLink[] = [
    ...[...sources].map(([id, s]) => ({
      source: `in:${id}`,
      target: "available",
      value: s.value,
      emphasis: id === "cash:DEPOSIT",
    })),
    ...[...uses].map(([id, u]) => ({
      source: "available",
      target: `out:${id}`,
      value: u.value,
      emphasis: id.startsWith("buy:") || id === "order:BUY",
    })),
  ];
  return { nodes, links, inflow, bought, excludedCurrencies: excluded };
}

/** Buys in `currency` split by the instrument's security type (from the holdings list). */
export function splitBuys(
  rows: readonly TransactionOut[],
  holdings: readonly { ticker: string; security_type: string | null }[],
  currency: string,
): BuySplit {
  const types = new Map(holdings.map((h) => [h.ticker, securityClass(h.security_type)]));
  const out: BuySplit = { stocks: 0, etfs: 0, other: 0 };
  for (const r of rows) {
    if (r.type !== "order" || r.subtype !== "BUY" || r.currency !== currency) continue;
    const cls = r.ticker ? types.get(r.ticker) ?? null : null;
    const v = -r.amount;
    if (cls === "etf") out.etfs += v;
    else if (cls === "stock") out.stocks += v;
    else out.other += v;
  }
  return out;
}

/** Every "YYYY-MM" from `from` to `to` inclusive. */
export function monthsBetween(from: string, to: string): string[] {
  const out: string[] = [];
  let y = Number(from.slice(0, 4));
  let m = Number(from.slice(5, 7));
  const endY = Number(to.slice(0, 4));
  const endM = Number(to.slice(5, 7));
  while (y < endY || (y === endY && m <= endM)) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    m++;
    if (m > 12) {
      m = 1;
      y++;
    }
  }
  return out;
}

export type MonthFlow = { month: string; buys: number; sells: number };

/** Bought and sold per calendar month (UTC), as positive amounts in `currency`. */
export function monthlyOrderFlows(
  rows: readonly TransactionOut[],
  currency: string,
  from: string,
  to: string,
): MonthFlow[] {
  const map = new Map(monthsBetween(from, to).map((m) => [m, { month: m, buys: 0, sells: 0 }]));
  for (const r of rows) {
    if (r.type !== "order" || r.currency !== currency) continue;
    const bucket = map.get(r.occurred_at.slice(0, 7));
    if (!bucket) continue;
    if (r.subtype === "BUY") bucket.buys += Math.abs(r.amount);
    else if (r.subtype === "SELL") bucket.sells += Math.abs(r.amount);
  }
  return [...map.values()];
}

export type Payer = { key: string; label: string; amount: number };

/** Dividends in `currency` per ticker, largest first; the tail past `top` folds into "Other". */
export function dividendsByTicker(rows: readonly TransactionOut[], currency: string, top = 5): Payer[] {
  const map = new Map<string, number>();
  for (const r of rows) {
    if (r.type !== "dividend" || r.currency !== currency) continue;
    const k = r.ticker ?? "—";
    map.set(k, (map.get(k) ?? 0) + r.amount);
  }
  const sorted = [...map.entries()].filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const head = sorted.slice(0, top).map(([key, amount]) => ({ key, label: key, amount }));
  const tail = sorted.slice(top);
  if (tail.length) head.push({ key: "__other", label: `Other ${tail.length}`, amount: tail.reduce((s, [, v]) => s + v, 0) });
  return head;
}

/** Sum of dividends in `currency` from totals. */
export function dividendTotal(totals: readonly TotalByCurrency[], currency: string): number {
  return totals.filter((t) => t.type === "dividend" && t.currency === currency).reduce((s, t) => s + t.amount, 0);
}
