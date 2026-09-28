import type { FeeTotals, TotalByCurrency, TransactionOut } from "@/lib/api/client";

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
  /** FX conversion, taxes & levies and account fees drawn in the graph (0 without fee data). */
  fees: number;
  /** Other currencies present in the totals and left out of the sums. */
  excludedCurrencies: string[];
};

/**
 * The period's fees in the graph's currency, from GET /portfolio/transactions `fees`:
 * fill charges split FX conversion / taxes & levies, account fees (cash FEE rows), and the
 * part of the fill fees Trading212 had already netted into order amounts.
 */
export type FeeFlow = {
  fx: number;
  taxes: number;
  account: number;
  includedInBuys: number;
  includedInSales: number;
};

/** The fee summary as a FeeFlow in `currency`, or null when it is in another currency. */
export function feeFlow(fees: FeeTotals | null | undefined, currency: string): FeeFlow | null {
  if (!fees || fees.currency !== currency) return null;
  return {
    fx: fees.fx_conversion,
    taxes: fees.taxes_levies,
    account: fees.account_fees,
    includedInBuys: fees.included_in_buys,
    includedInSales: fees.included_in_sales,
  };
}

export type FeeNode = { id: string; label: string; value: number };

/**
 * The fee nodes of the cash-flow graph: one "FX & fees" node when only one kind of charge
 * is present, otherwise one node per kind (FX conversion / Taxes & levies / Account fees).
 */
export function feeNodes(fees: FeeFlow): FeeNode[] {
  const parts: FeeNode[] = [
    { id: "fees:fx", label: "FX conversion", value: fees.fx },
    { id: "fees:tax", label: "Taxes & levies", value: fees.taxes },
    { id: "fees:account", label: "Account fees", value: fees.account },
  ].filter((p) => p.value > 0);
  if (parts.length === 1) return [{ id: "fees", label: "FX & fees", value: parts[0].value }];
  return parts;
}

/** What the fee summary's `status` means for the reader; null when fees are fully read. */
export function feeStatusNote(fees: Pick<FeeTotals, "status" | "fills" | "fills_captured"> | null | undefined): string | null {
  if (!fees) return null;
  if (fees.status === "not_captured") return "No fee data yet: Trading212 fill fees are backfilled on the next sync.";
  if (fees.status === "partial")
    return `Fees read for ${fees.fills_captured} of ${fees.fills} orders; the rest are backfilled on the next sync.`;
  return null;
}

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
 *
 * With `fees`, the fees are uses of their own ("FX & fees", or split by kind). Trading212's
 * order amount (netValue) excludes fill fees as documented, so a buy took amount + fees out
 * of cash and a sale paid out amount - fees: adding the fee node makes "Cash kept" right.
 * Where the backend found the fees already inside the amounts (`includedIn*`), they are
 * taken out of buys / added back to sales first, so no fee is counted twice. Cash FEE rows
 * are drawn only in the fee node (not also as a "Fee" use).
 */
export function cashFlowGraph(
  totals: readonly TotalByCurrency[],
  currency: string,
  split?: BuySplit | null,
  fees?: FeeFlow | null,
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
    if (fees && t.type === "cash" && t.subtype === "FEE") continue;
    if (t.type === "dividend") {
      if (t.amount > 0) add(sources, "dividend", "Dividends", t.amount);
      else add(uses, "dividend", "Dividend adjustments", -t.amount);
      continue;
    }
    const key = `${t.type}:${t.subtype ?? ""}`;
    if (t.amount > 0) add(sources, key, SOURCE_LABEL[key] ?? humanizeSubtype(t.subtype), t.amount);
    else add(uses, key, USE_LABEL[key] ?? humanizeSubtype(t.subtype), -t.amount);
  }

  const booked = uses.get("order:BUY")?.value ?? 0;
  const splitOk =
    split && booked > 0 && Math.abs(split.stocks + split.etfs + split.other - booked) <= Math.max(1, booked * 1e-6);
  // Fees Trading212 had already put inside the buys' amounts move to the fee node.
  const inBuys = fees ? Math.min(Math.max(fees.includedInBuys, 0), booked) : 0;
  const bought = booked - inBuys;
  if (inBuys > 0) uses.set("order:BUY", { label: uses.get("order:BUY")!.label, value: bought });
  const inSales = fees ? Math.max(fees.includedInSales, 0) : 0;
  if (inSales > 0) add(sources, "order:SELL", SOURCE_LABEL["order:SELL"], inSales);
  if (splitOk) {
    uses.delete("order:BUY");
    const scale = booked > 0 ? bought / booked : 1;
    const parts: [string, string, number][] = [
      ["buy:stock", "Stocks", split!.stocks * scale],
      ["buy:etf", "ETFs", split!.etfs * scale],
      ["buy:other", "Other buys", split!.other * scale],
    ];
    const rest = new Map(uses);
    uses.clear();
    for (const [k, label, v] of parts) if (v > 0) uses.set(k, { label, value: v });
    for (const [k, v] of rest) uses.set(k, v);
  }

  const fee = fees ? feeNodes(fees) : [];
  for (const f of fee) uses.set(f.id, { label: f.label, value: f.value });

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
  return { nodes, links, inflow, bought, fees: fee.reduce((s, f) => s + f.value, 0), excludedCurrencies: excluded };
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

export type FlowPeriod = { id: "quarter" | "ytd" | "1y"; label: string; title: string; from: string; to: string };

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** The Transactions charts' periods ending `today` (UTC): this quarter, year to date, last 12 months. */
export function flowPeriods(today: Date = new Date()): FlowPeriod[] {
  const y = today.getUTCFullYear();
  const q = Math.floor(today.getUTCMonth() / 3);
  const yearAgo = new Date(today);
  yearAgo.setUTCFullYear(y - 1);
  yearAgo.setUTCDate(yearAgo.getUTCDate() + 1);
  const to = isoDay(today);
  return [
    { id: "quarter", label: `Q${q + 1}`, title: `Q${q + 1} ${y}`, from: `${y}-${String(q * 3 + 1).padStart(2, "0")}-01`, to },
    { id: "ytd", label: "YTD", title: `${y} YTD`, from: `${y}-01-01`, to },
    { id: "1y", label: "1Y", title: "Last 12 months", from: isoDay(yearAgo), to },
  ];
}

/** The same window one year earlier (for a year-on-year comparison). */
export function yearEarlier(period: { from: string; to: string }): { from: string; to: string } {
  const shift = (iso: string) => {
    const d = new Date(`${iso}T00:00:00Z`);
    d.setUTCFullYear(d.getUTCFullYear() - 1);
    return isoDay(d);
  };
  return { from: shift(period.from), to: shift(period.to) };
}

/** Sum of dividends in `currency` from totals. */
export function dividendTotal(totals: readonly TotalByCurrency[], currency: string): number {
  return totals.filter((t) => t.type === "dividend" && t.currency === currency).reduce((s, t) => s + t.amount, 0);
}
