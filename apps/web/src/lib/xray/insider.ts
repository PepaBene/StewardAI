/*
 * Insider activity (Forms 3/4/5) by economic class. Only open-market buys and sells count
 * toward net discretionary activity; grants, option exercises, gifts and tax withholding are
 * shown beside them, never merged into a "net buying" figure.
 */
import type { DataState } from "@/lib/data-state/states";

import { toNum } from "./parse";
import { daysBetween } from "./ownership";
import type { DataProvenance } from "@/lib/data-state/provenance";
import type { HoldingPdmrOut, InsiderActivityOut, InsiderTransactionRow } from "./types";

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
  discretionary: "Open-market trades (only rows the backend counts go into the net figure)",
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
  /**
   * Sum of reported shares; null when no row of the class carries a share count. Derivative legs
   * of an option exercise or conversion are left out: the same exercise also reports a stock row,
   * so adding both would double the shares.
   */
  shares: number | null;
  rowsWithoutShares: number;
  /** Derivative-leg rows of an exercise/conversion, counted in `rows` but not in `shares`. */
  derivativeLegRows: number;
  /** Rows the backend counts toward net discretionary activity (row-level verdict). */
  countedRows: number;
  /** Rows of an open-market class that the backend excludes, by reason (for example a 10b5-1 plan). */
  exclusions: { reason: string; rows: number }[];
};

const ORDER = Object.keys(INSIDER_CLASS_META);
/** Classes whose derivative row and stock row describe the same event. */
const TWO_LEG_CLASSES = new Set(["option_exercise", "conversion"]);

/** Superseded (amended-away) rows are left out so an amended filing is not counted twice. */
export function classTotals(rows: readonly InsiderTransactionRow[]): { totals: ClassTotal[]; superseded: number } {
  const live = rows.filter((r) => !r.superseded_by);
  const acc = new Map<string, ClassTotal>();
  const reasons = new Map<string, Map<string, number>>();
  for (const r of live) {
    const key = r.economic_class ?? "other";
    const meta = classMeta(key);
    const cur =
      acc.get(key) ??
      { economicClass: key, label: meta.label, group: meta.group, rows: 0, shares: null, rowsWithoutShares: 0, derivativeLegRows: 0, countedRows: 0, exclusions: [] };
    cur.rows += 1;
    if (r.counts_toward_net_discretionary === true) cur.countedRows += 1;
    else if (meta.group === "discretionary") {
      const reason = r.exclusion_reason ?? "excluded";
      const m = reasons.get(key) ?? new Map<string, number>();
      m.set(reason, (m.get(reason) ?? 0) + 1);
      reasons.set(key, m);
    }
    if (TWO_LEG_CLASSES.has(key) && r.derivative === true) {
      cur.derivativeLegRows += 1;
    } else {
      const s = toNum(r.shares);
      if (s === null) cur.rowsWithoutShares += 1;
      else cur.shares = (cur.shares ?? 0) + s;
    }
    acc.set(key, cur);
  }
  for (const [key, m] of reasons) {
    const t = acc.get(key);
    if (t) t.exclusions = [...m].map(([reason, n]) => ({ reason, rows: n }));
  }
  const rank = (c: string) => (ORDER.indexOf(c) < 0 ? ORDER.length : ORDER.indexOf(c));
  const totals = [...acc.values()].sort((a, b) => rank(a.economicClass) - rank(b.economicClass));
  return { totals, superseded: rows.length - live.length };
}

export const EXCLUSION_LABEL: Record<string, string> = {
  pre_planned_10b5_1: "Rule 10b5-1 plan",
  derivative_security: "derivative security",
  no_share_count: "no share count",
  superseded_by_amendment: "superseded",
};

/** "yes" / "no" / "2 of 3 rows" for the class table's "Counts toward net" column. */
export function countsTowardNetText(t: ClassTotal): string {
  if (t.group !== "discretionary") return "no";
  if (t.countedRows === t.rows) return t.rows > 0 ? "yes" : "no";
  if (t.countedRows === 0) return `no (all ${t.rows} rows excluded)`;
  return `${t.countedRows} of ${t.rows} rows`;
}

export function exclusionText(t: ClassTotal): string {
  return t.exclusions.map((e) => `${e.rows} ${EXCLUSION_LABEL[e.reason] ?? e.reason.replace(/_/g, " ")}`).join(", ");
}

/** The window end is not the data's date: the newest stored filing is. */
export const INSIDER_STALE_DAYS = 120;

export function insiderProvenance(data: InsiderActivityOut, opts: { lastGood: boolean; rowLimit: number; today?: Date }): DataProvenance {
  const nd = data.net_discretionary;
  const hasRows = data.transactions.length > 0;
  const truncated = data.transactions.length >= opts.rowLimit;
  const newest = data.transactions.map((t) => t.filed).sort().pop() ?? null;
  const ref = nd.until ?? (opts.today ?? new Date()).toISOString().slice(0, 10);
  const age = newest ? daysBetween(newest, ref) : null;
  const quiet = age !== null && age > INSIDER_STALE_DAYS;
  const reasons: string[] = [];
  if (opts.lastGood) reasons.push("The refresh failed; this is the last value that loaded.");
  else if (!hasRows) reasons.push("No insider transaction is stored for this holding in the window. None loaded is not the same as no insider trading.");
  else {
    if (truncated) reasons.push(`Only the newest ${opts.rowLimit} rows were requested; older rows in the window are not counted in the class table.`);
    if (nd.plan_status_unknown_rows > 0) reasons.push(`${nd.plan_status_unknown_rows} counted rows do not state whether a Rule 10b5-1 plan applied.`);
    if (quiet) reasons.push(`The newest stored filing is ${age} days older than the window end (${ref}); a quiet period and filings not yet loaded look the same.`);
  }
  return {
    source: "SEC EDGAR Forms 3/4/5, classified by economic purpose (StewardAI)",
    asOf: newest ?? nd.until,
    knownAt: newest,
    method: "A transaction code says how a trade was reported, not why: code and footnotes map to an economic class. Only open-market buys and sells that are non-derivative, have a share count and are not under a Rule 10b5-1 plan count toward the net figure.",
    state: opts.lastGood ? "unavailable" : !hasRows ? "not_covered" : quiet ? "stale" : nd.plan_status_unknown_rows > 0 || truncated ? "partial" : "verified",
    reason: reasons.length > 0 ? reasons.join(" ") : null,
    coverage: null,
  };
}

/** Why net value is missing: no counted row at all is a different statement from an unpriced row. */
export function netValueText(nd: InsiderActivityOut["net_discretionary"], compactValue: string): string {
  if (nd.buy_rows + nd.sell_rows === 0) return "is not computed because no row counted toward the net figure";
  if (nd.net_value === null) return "is not computed because some counted rows have no price";
  return `${compactValue} at reported prices`;
}

export function netValueNote(nd: InsiderActivityOut["net_discretionary"]): string {
  if (nd.buy_rows + nd.sell_rows === 0) return "no row counted toward the net figure";
  return nd.value_complete ? "every counted row has a price" : "value incomplete: some rows have no price";
}

/**
 * The PDMR state of THIS holding, from the API's `ticker_status` (counted for this ticker only,
 * case-insensitively). `status` is channel-wide (all issuers) and only says whether the channel
 * is accessible. An old response without `ticker_status` falls back to this holding's own rows.
 */
export function pdmrHoldingState(data: HoldingPdmrOut): { state: DataState; reason: string | null; parsed: number; total: number } {
  const ts = data.ticker_status;
  const total = ts ? ts.attempted : data.notifications.length;
  const parsed = ts ? ts.parsed : data.notifications.filter((n) => n.status === "parsed").length;
  const own = ts ?? data.status;
  if (!ts && data.status.state !== "available") return { state: pdmrState(data.status), reason: data.status.detail, parsed, total };
  if (total === 0) return { state: "not_covered", reason: "No PDMR announcement is stored for this holding (the status counts below are for all issuers).", parsed, total };
  if (own.state !== "available") return { state: pdmrState(own), reason: own.detail, parsed, total };
  if (parsed < total) return { state: "partial", reason: `${total - parsed} of this holding's ${total} announcements could not be read.`, parsed, total };
  return { state: "verified", reason: null, parsed, total };
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
