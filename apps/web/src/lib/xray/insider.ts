/*
 * Insider activity (Forms 3/4/5) by economic class. Only open-market buys and sells count
 * toward net discretionary activity; grants, option exercises, gifts and tax withholding are
 * shown beside them, never merged into a "net buying" figure.
 */
import type { DataState } from "@/lib/data-state/states";

import { toNum } from "./parse";
import type { HoldingPdmrOut, InsiderTransactionRow } from "./types";

export type InsiderGroup = "discretionary" | "compensation" | "non_market" | "other";

export const INSIDER_CLASS_META: Record<string, { label: string; group: InsiderGroup }> = {
  open_market_buy: { label: "Open-market buy", group: "discretionary" },
  open_market_sell: { label: "Open-market sell", group: "discretionary" },
  option_exercise: { label: "Option exercise", group: "compensation" },
  grant_award: { label: "Grant / award", group: "compensation" },
  tax_withholding: { label: "Tax withholding / exercise payment", group: "compensation" },
  conversion: { label: "Conversion", group: "compensation" },
  gift: { label: "Gift", group: "non_market" },
  holding_snapshot: { label: "Holding snapshot (no transaction)", group: "non_market" },
  other: { label: "Other / unclassified code", group: "other" },
};

export const INSIDER_GROUP_LABEL: Record<InsiderGroup, string> = {
  discretionary: "Counts toward net discretionary activity",
  compensation: "Compensation mechanics (excluded)",
  non_market: "Not a market transaction (excluded)",
  other: "Other codes (excluded)",
};

export function classMeta(cls: string | null): { label: string; group: InsiderGroup } {
  return (cls && INSIDER_CLASS_META[cls]) || { label: cls ?? "Unclassified", group: "other" };
}

export type ClassTotal = {
  economicClass: string;
  label: string;
  group: InsiderGroup;
  rows: number;
  /** Sum of reported shares; null when no row of the class carries a share count. */
  shares: number | null;
  rowsWithoutShares: number;
};

const ORDER = Object.keys(INSIDER_CLASS_META);

/** Superseded (amended-away) rows are left out so an amended filing is not counted twice. */
export function classTotals(rows: readonly InsiderTransactionRow[]): { totals: ClassTotal[]; superseded: number } {
  const live = rows.filter((r) => !r.superseded_by);
  const acc = new Map<string, ClassTotal>();
  for (const r of live) {
    const key = r.economic_class ?? "other";
    const meta = classMeta(key);
    const cur = acc.get(key) ?? { economicClass: key, label: meta.label, group: meta.group, rows: 0, shares: null, rowsWithoutShares: 0 };
    cur.rows += 1;
    const s = toNum(r.shares);
    if (s === null) cur.rowsWithoutShares += 1;
    else cur.shares = (cur.shares ?? 0) + s;
    acc.set(key, cur);
  }
  const rank = (c: string) => (ORDER.indexOf(c) < 0 ? ORDER.length : ORDER.indexOf(c));
  const totals = [...acc.values()].sort((a, b) => rank(a.economicClass) - rank(b.economicClass));
  return { totals, superseded: rows.length - live.length };
}

export function pdmrState(s: HoldingPdmrOut["status"]): DataState {
  switch (s.state) {
    case "available":
      return s.parsed === 0 && s.attempted > 0 ? "partial" : "verified";
    case "not_covered":
      return "not_covered";
    case "paid_only":
      return "paid_only";
    case "access_unverified":
      return "access_unverified";
    case "quota_exhausted":
      return "quota_exhausted";
    case "disputed":
      return "disputed";
    default:
      return "unavailable";
  }
}

export const PDMR_STATUS_LABEL: Record<string, string> = {
  parsed: "Parsed",
  no_template: "Could not be read (no template)",
  access_unverified: "Access unverified",
  paid_only: "Paid only",
  quota_exhausted: "Quota exhausted",
  temporarily_failed: "Temporarily failed",
};
