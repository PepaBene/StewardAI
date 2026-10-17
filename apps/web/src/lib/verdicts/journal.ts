import type { Verdict } from "@/components/insight/verdict-line";

/*
 * The decision journal in words: how each decision has fared since, read the way the decision
 * points (a rise after a buy is good, after a sale it is what selling cost), and whether the
 * ledger holds the reasons the scorecard needs.
 */

export type VerdictResult = { tone: Verdict; text: string };

type Entry = {
  decision: string;
  ticker?: string | null;
  hypothesis: string;
  price_at_decision?: number | string | null;
  price_currency?: string | null;
};

export type Quote = { price: number; currency: string };

/** The broker sync's placeholder: an entry logged from a trade with no reason written yet. */
export const AUTO_LOGGED = "Logged automatically from your broker sync.";

const LONG = new Set(["buy", "add", "hold"]);
const SHORT = new Set(["sell", "trim", "pass"]);

export function isAutoLogged(entry: Pick<Entry, "hypothesis">): boolean {
  return entry.hypothesis.startsWith(AUTO_LOGGED);
}

/** "1,587.60 EUR": a stored decision price without its float noise. */
export function priceText(price: number | string | null | undefined, currency?: string | null): string | null {
  const p = typeof price === "string" ? Number(price) : price;
  if (p == null || !Number.isFinite(p)) return null;
  const digits = p >= 100 ? 2 : p >= 1 ? 2 : 4;
  return `${p.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}${currency ? ` ${currency}` : ""}`;
}

/** The price move since the decision, in the decision's own currency; null when not comparable. */
export function moveSince(entry: Entry, quote: Quote | undefined): number | null {
  const p = Number(entry.price_at_decision);
  if (!quote || !Number.isFinite(p) || p <= 0) return null;
  if (entry.price_currency && entry.price_currency !== quote.currency) return null;
  return quote.price / p - 1;
}

function pct(x: number): string {
  return `${x > 0 ? "+" : x < 0 ? "−" : "±"}${(Math.abs(x) * 100).toFixed(1)}%`;
}

/** One decision since it was made: "Up 4.2% since you bought: the call is working so far." */
export function sinceText(entry: Entry, quote: Quote | undefined): VerdictResult | null {
  const m = moveSince(entry, quote);
  if (m == null) return null;
  if (LONG.has(entry.decision)) {
    return {
      tone: m >= 0 ? "good" : "caution",
      text: `${pct(m)} since you ${entry.decision === "hold" ? "kept it" : entry.decision === "add" ? "added" : "bought"} (price only, before currency and dividends).`,
    };
  }
  if (SHORT.has(entry.decision)) {
    const verb = entry.decision === "pass" ? "passed" : entry.decision === "trim" ? "trimmed" : "sold";
    return {
      tone: m <= 0 ? "good" : "caution",
      text: `${pct(m)} since you ${verb}: ${m <= 0 ? "staying out has paid so far" : "staying in would have earned this so far"}.`,
    };
  }
  return null;
}

/** The ledger in one sentence: what it can be judged on and what it is missing. */
export function journalVerdict(entries: readonly Entry[], quotes: ReadonlyMap<string, Quote>): VerdictResult | null {
  if (entries.length === 0) return null;
  const auto = entries.filter(isAutoLogged).length;
  const moves = entries
    .filter((e) => LONG.has(e.decision))
    .map((e) => moveSince(e, e.ticker ? quotes.get(e.ticker) : undefined))
    .filter((m): m is number => m != null);
  const working = moves.filter((m) => m >= 0).length;
  const parts: string[] = [];
  if (moves.length > 0) parts.push(`${working} of ${moves.length} buys are above their purchase price so far`);
  if (auto > 0) {
    parts.push(
      `${auto === entries.length ? (auto === 1 ? "the decision was" : `all ${auto} decisions were`) : `${auto} of ${entries.length} decisions ${auto === 1 ? "was" : "were"}`} logged by the broker sync with no reason written, so the scorecard can measure outcomes but not your reasoning. Use "Review this" to add why you made each one while you still remember`,
    );
  }
  return { tone: auto > 0 ? "caution" : "neutral", text: `${parts.join("; ")}.`.replace(/^./, (c) => c.toUpperCase()) };
}
