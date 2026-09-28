import type { Holding } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import { formatCompact } from "@/lib/charts/format";

/*
 * Derivations for the Holdings screen: the KPI tiles, the day-move lookups, the daily-move
 * grid and the weight meter scale. Everything is computed from API figures; nothing is
 * filled in where a figure is missing (the result is null / left out instead).
 */

export type HoldingPrices = components["schemas"]["HoldingPrices"];
export type DatedClose = components["schemas"]["PricePoint"];

/**
 * The exchange-free symbol of a Trading212 ticker: "VWCEd_EQ" -> "VWCE", "MSFT_US_EQ" ->
 * "MSFT". The trailing lower-case letters are Trading212's venue suffix; a ticker that does
 * not follow the pattern is returned unchanged.
 */
export function displayTicker(ticker: string): string {
  const stripped = ticker
    .replace(/_EQ$/, "")
    .replace(/_[A-Z]{2}$/, "")
    .replace(/[a-z]+$/, "");
  return stripped || ticker;
}

export type SecurityClass = "ETF" | "Stock" | "Other";

/**
 * OpenFIGI's security type folded into the classes the screen shows. ETPs, ETFs, ETNs and
 * funds are "ETF"; common and preferred stock, ADRs and depositary receipts are "Stock".
 * Null while the reference-data job has not resolved the instrument.
 */
export function securityClass(type: string | null | undefined): SecurityClass | null {
  if (!type) return null;
  const t = type.toLowerCase();
  if (/\b(etp|etf|etn|fund|unit)\b/.test(t) || t.includes("exchange traded")) return "ETF";
  if (t.includes("stock") || t.includes("adr") || t.includes("depositary") || t.includes("share") || t.includes("reit")) {
    return "Stock";
  }
  return "Other";
}

function plural(n: number, one: string, many: string) {
  return `${n} ${n === 1 ? one : many}`;
}

/** "2 ETFs · 10 stocks" (plus "1 other" / "1 unresolved" when there are any). */
export function positionMixLabel(holdings: readonly Pick<Holding, "security_type">[]): string {
  const counts = { ETF: 0, Stock: 0, Other: 0, unknown: 0 };
  for (const h of holdings) counts[securityClass(h.security_type) ?? "unknown"] += 1;
  const parts = [plural(counts.ETF, "ETF", "ETFs"), plural(counts.Stock, "stock", "stocks")];
  if (counts.Other) parts.push(`${counts.Other} other`);
  if (counts.unknown) parts.push(`${counts.unknown} unresolved`);
  return parts.join(" · ");
}

export function priceIndex(prices: readonly HoldingPrices[] | null | undefined): Map<string, HoldingPrices> {
  return new Map((prices ?? []).map((p) => [p.ticker, p]));
}

/**
 * The positions' combined close-to-close move, weighted by value: Σ value × d / (1 + d)
 * over Σ value of the positions that have a day change (the value before the move is
 * value / (1 + d)). Null when no position has one.
 */
export function portfolioDayMove(
  holdings: readonly Pick<Holding, "ticker" | "value_base">[],
  dayChange: (ticker: string) => number | null | undefined,
): { change: number; covered: number } | null {
  let before = 0;
  let after = 0;
  let covered = 0;
  for (const h of holdings) {
    const d = dayChange(h.ticker);
    if (typeof d !== "number" || !Number.isFinite(d) || d <= -1) continue;
    after += h.value_base;
    before += h.value_base / (1 + d);
    covered += 1;
  }
  if (covered === 0 || before <= 0) return null;
  return { change: after / before - 1, covered };
}

/** Total unrealised P/L and its share of cost; null when positions report P/L in more than one currency. */
export function unrealisedTotal(
  holdings: readonly Pick<Holding, "ppl" | "cost_account" | "account_currency">[],
): { ppl: number; pct: number | null; currency: string } | null {
  if (holdings.length === 0) return null;
  const currency = holdings[0].account_currency;
  if (holdings.some((h) => h.account_currency !== currency)) return null;
  const ppl = holdings.reduce((s, h) => s + h.ppl, 0);
  const cost = holdings.reduce((s, h) => s + h.cost_account, 0);
  return { ppl, pct: cost > 0 ? ppl / cost : null, currency };
}

/** The best and worst day movers among holdings that have a day change. */
export function dayExtremes<T extends { ticker: string }>(
  holdings: readonly T[],
  dayChange: (ticker: string) => number | null | undefined,
): { best: { holding: T; change: number }; worst: { holding: T; change: number } } | null {
  const moved = holdings
    .map((holding) => ({ holding, change: dayChange(holding.ticker) }))
    .filter((m): m is { holding: T; change: number } => typeof m.change === "number" && Number.isFinite(m.change));
  if (moved.length === 0) return null;
  const sorted = [...moved].sort((a, b) => b.change - a.change);
  return { best: sorted[0], worst: sorted[sorted.length - 1] };
}

/** Change from the first to the last close in the bars; null with fewer than two. */
export function periodChange(bars: readonly DatedClose[]): number | null {
  if (bars.length < 2 || !(bars[0].close > 0)) return null;
  return bars[bars.length - 1].close / bars[0].close - 1;
}

export type DailyMoveRow = { ticker: string; label: string; cells: (number | null)[] };

/**
 * Holdings × the last `sessions` trading dates seen in any holding's bars: each cell is that
 * holding's close-to-close return on that date (its close over its own previous close).
 * A date the holding has no close for, or its first bar, is null.
 */
export function dailyMoveGrid(
  prices: readonly HoldingPrices[],
  sessions = 20,
): { dates: string[]; rows: DailyMoveRow[] } {
  const all = new Set<string>();
  for (const p of prices) for (const b of p.bars) all.add(b.date);
  const dates = [...all].sort().slice(-sessions);
  const rows = prices
    .filter((p) => p.bars.length > 1)
    .map((p) => {
      const byDate = new Map<string, number>();
      p.bars.forEach((b, i) => {
        const prev = p.bars[i - 1];
        if (prev && prev.close > 0) byDate.set(b.date, b.close / prev.close - 1);
      });
      return { ticker: p.ticker, label: displayTicker(p.ticker), cells: dates.map((d) => byDate.get(d) ?? null) };
    });
  return { dates, rows };
}

/**
 * Right end of the weight meters: the largest weight or target plus a 10% margin, rounded
 * up to a whole 5 percentage points (0.221 -> 0.25). At least 5%.
 */
export function weightScale(holdings: readonly Pick<Holding, "weight" | "target_weight">[]): number {
  const top = Math.max(0, ...holdings.flatMap((h) => [h.weight ?? 0, h.target_weight ?? 0]));
  return Math.max(0.05, Math.ceil((top * 1.1 * 100) / 5 - 1e-9) * 0.05);
}

/** Sum of the positions' weights: the invested share of the weight basis. */
export function investedShare(holdings: readonly Pick<Holding, "weight">[]): number | null {
  const weights = holdings.map((h) => h.weight).filter((w): w is number => typeof w === "number");
  return weights.length ? weights.reduce((s, w) => s + w, 0) : null;
}

/** Money in compact form with its currency code: 2_381_884 CZK -> "2.38M CZK". */
export function compactMoney(value: number, currency: string, signed = false): string {
  return `${signed && value > 0 ? "+" : ""}${formatCompact(value)} ${currency}`.trim();
}
