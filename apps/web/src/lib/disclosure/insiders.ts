import type { DivergingBarRow } from "@/components/charts";
import type { components } from "@/lib/api/schema";

/*
 * Insider activity (plan P4): types and pure logic for the page. Same rules as the backend's
 * `disclosure/insiders.py`: only open-market buys and sells on non-derivative lines are discretionary,
 * an exercise plus a same-day sale is a bundle that stays out of the net, plan trades are counted
 * apart, and nothing here says why anyone traded.
 */

type S = components["schemas"];
export type InsiderActivity = S["DisclosureInsiderActivityOut"];
export type InsiderTx = S["InsiderTxOut"];
export type InsiderNet = S["InsiderNetOut"];
export type PdmrActivity = S["PdmrActivityOut"];

export const WINDOWS = [30, 90, 365] as const;
export type Window = (typeof WINDOWS)[number];

export const ROLES = ["officer", "director", "ten_percent_owner", "other", "all"] as const;
export type RoleKey = (typeof ROLES)[number];
export const ROLE_LABEL: Record<RoleKey, string> = {
  officer: "Officers",
  director: "Directors",
  ten_percent_owner: "10% owners",
  other: "Other insiders",
  all: "All insiders",
};

export type ClassKey = "open_market_buy" | "open_market_sell" | "option_exercise" | "grant_award" | "gift" | "tax_withholding" | "conversion" | "other" | "holding_snapshot" | "unclassified";

export type Mark = "triangle-up" | "triangle-down" | "diamond" | "circle" | "square" | "plus" | "cross";

/** Each class has its own shape and its own label, so none is told apart by colour alone. */
export const CLASS_META: Record<ClassKey, { label: string; mark: Mark; filled: boolean; discretionary: boolean }> = {
  open_market_buy: { label: "Open-market purchase", mark: "triangle-up", filled: true, discretionary: true },
  open_market_sell: { label: "Open-market sale", mark: "triangle-down", filled: true, discretionary: true },
  grant_award: { label: "Award or grant", mark: "diamond", filled: false, discretionary: false },
  option_exercise: { label: "Option exercise", mark: "circle", filled: false, discretionary: false },
  tax_withholding: { label: "Shares withheld for tax", mark: "square", filled: false, discretionary: false },
  gift: { label: "Gift", mark: "plus", filled: false, discretionary: false },
  conversion: { label: "Conversion", mark: "circle", filled: true, discretionary: false },
  other: { label: "Other", mark: "cross", filled: false, discretionary: false },
  holding_snapshot: { label: "Holding report", mark: "cross", filled: false, discretionary: false },
  unclassified: { label: "Not classified", mark: "cross", filled: false, discretionary: false },
};

export const classOf = (t: Pick<InsiderTx, "economic_class">): ClassKey => (t.economic_class in CLASS_META ? (t.economic_class as ClassKey) : "unclassified");

export const PLAN_LABEL: Record<string, string> = {
  plan: "Under a 10b5-1 plan",
  not_plan: "Not under a plan",
  unknown: "Plan status not reported",
};

export const num = (v: string | null | undefined): number | null => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

export const formatUsd = (v: number | string | null | undefined): string => {
  const n = typeof v === "string" ? num(v) : (v ?? null);
  if (n == null) return "—";
  const sign = n < 0 ? "−" : "";
  return `${sign}$${new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(Math.abs(n))}`;
};

export const formatQty = (v: string | null | undefined): string => {
  const n = num(v);
  return n == null ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(n);
};

const DAY = 86_400_000;

/** Rows known by the cutoff. The caller's `known_at` is the filing day's end, never the trade date. */
export const insidersKnown = (txs: readonly InsiderTx[], cutoffMs: number) => txs.filter((t) => Date.parse(t.known_at) <= cutoffMs);

export type NetRowOut = { role: RoleKey; window: Window; netValue: number; buyValue: number; sellValue: number; insiders: number; trades: number; planExcluded: number; planUnknown: number; bundled: number };

/** The discretionary net per role and window, from the transaction list. Mirrors `net_by_role`. */
export function netFromTransactions(txs: readonly InsiderTx[], cutoffMs: number): NetRowOut[] {
  const known = insidersKnown(txs, cutoffMs);
  const out: NetRowOut[] = [];
  for (const window of WINDOWS) {
    for (const role of ROLES) {
      const inRole = known.filter((t) => (role === "all" || t.role === role) && t.tx_date != null && Date.parse(t.tx_date) <= cutoffMs && Date.parse(t.tx_date) > cutoffMs - window * DAY);
      const tradeRows = inRole.filter((t) => (t.economic_class === "open_market_buy" || t.economic_class === "open_market_sell") && !t.derivative);
      const buy = inRole.filter((t) => t.economic_class === "open_market_buy" && t.countable && !t.bundled);
      const sell = inRole.filter((t) => t.economic_class === "open_market_sell" && t.countable && !t.bundled);
      const sum = (rows: InsiderTx[]) => rows.reduce((a, t) => a + (num(t.value) ?? 0), 0);
      const people = new Set([...buy, ...sell].map((t) => t.insider));
      out.push({
        role,
        window,
        buyValue: sum(buy),
        sellValue: sum(sell),
        netValue: sum(buy) - sum(sell),
        insiders: people.size,
        trades: buy.length + sell.length,
        // Same order as `net_by_role`: bundled is decided first, then plan, over open-market lines only.
        planExcluded: tradeRows.filter((t) => !t.bundled && t.plan === "plan").length,
        planUnknown: [...buy, ...sell].filter((t) => t.plan === "unknown").length,
        bundled: tradeRows.filter((t) => t.bundled).length,
      });
    }
  }
  return out;
}

/** The server's own net rows (`view.net`) as page rows, so live data does not depend on this file's copy of the rules. */
export function netFromApi(net: readonly InsiderNet[]): NetRowOut[] {
  return net
    .filter((n) => (WINDOWS as readonly number[]).includes(n.window_days) && (ROLES as readonly string[]).includes(n.role))
    .map((n) => ({
      role: n.role as RoleKey,
      window: n.window_days as Window,
      netValue: num(n.net_value) ?? 0,
      buyValue: num(n.buy_value) ?? 0,
      sellValue: num(n.sell_value) ?? 0,
      insiders: n.insiders,
      trades: n.trades,
      planExcluded: n.plan_trades_excluded,
      planUnknown: n.plan_unknown_trades,
      bundled: n.bundled_sales_excluded,
    }));
}

/** Bars for one window. A role with no discretionary trade has no bar (null) and says "no trades": missing is not a zero net. */
export function netBars(rows: readonly NetRowOut[], window: Window): DivergingBarRow[] {
  return ROLES.filter((r) => r !== "all").map((role) => {
    const r = rows.find((x) => x.role === role && x.window === window);
    const traded = r != null && r.trades > 0;
    return { label: traded ? ROLE_LABEL[role] : `${ROLE_LABEL[role]} (no trades)`, value: traded ? r.netValue : null };
  });
}

export type StripEvent = { id: string; x: number; xFiled: number; cls: ClassKey; label: string; known: boolean };

/** Event positions on a shared date axis, 0 to 1. `xFiled` is where the filing landed, so the lag is visible. */
export function stripEvents(txs: readonly InsiderTx[], cutoffMs: number): { events: StripEvent[]; from: number; to: number } {
  const dated = txs.filter((t) => t.tx_date != null);
  if (dated.length === 0) return { events: [], from: cutoffMs - 90 * DAY, to: cutoffMs };
  const times = dated.flatMap((t) => [Date.parse(t.tx_date as string), Date.parse(t.filed)]);
  const from = Math.min(...times);
  const to = Math.max(...times, cutoffMs);
  const span = Math.max(to - from, 1);
  const events = dated
    .map((t) => ({
      id: `${t.accession}:${t.row_index}`,
      x: (Date.parse(t.tx_date as string) - from) / span,
      xFiled: (Date.parse(t.filed) - from) / span,
      cls: classOf(t),
      label: `${CLASS_META[classOf(t)].label}, ${t.insider}, ${t.tx_date}, filed ${t.filed}`,
      known: Date.parse(t.known_at) <= cutoffMs,
    }))
    .sort((a, b) => a.x - b.x);
  return { events, from, to };
}

/** The classes actually present, in the order a reader should scan them. */
export function classesPresent(txs: readonly InsiderTx[]): ClassKey[] {
  const order = Object.keys(CLASS_META) as ClassKey[];
  const have = new Set(txs.map(classOf));
  return order.filter((c) => have.has(c));
}
