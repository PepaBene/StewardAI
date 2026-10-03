/*
 * Fund-level read models: coverage ledger tiers, pairwise overlap, fee drag, characteristics
 * audit verdicts and factsheet history. Pure helpers over the typed/parsed API responses.
 */
import type { DataProvenance } from "@/lib/data-state/provenance";
import type { DataState } from "@/lib/data-state/states";

import type {
  AuditRow,
  AuditVerdict,
  CoverageLedger,
  FactsheetRow,
  FundOverlapReport,
  FundPairOverlap,
  LedgerRow,
  LedgerTier,
} from "./types";

// ---- coverage ledger -------------------------------------------------------------------------

export const LEDGER_TIERS: readonly LedgerTier[] = [
  "official_holdings",
  "issuer_downloadable",
  "regulatory_filing",
  "proxy_fund",
  "unavailable",
];

export const LEDGER_TIER_META: Record<LedgerTier, { label: string; short: string; state: DataState; description: string }> = {
  official_holdings: {
    label: "Official holdings",
    short: "Official",
    state: "verified",
    description: "The fund's or index provider's own published constituent list.",
  },
  issuer_downloadable: {
    label: "Issuer downloadable data",
    short: "Issuer",
    state: "verified",
    description: "Holdings from the fund issuer's downloadable file or product-data endpoint (undocumented endpoints can change).",
  },
  regulatory_filing: {
    label: "Regulatory filing",
    short: "Regulatory",
    state: "partial",
    description: "Holdings from a regulator filing (for example SEC N-PORT): dated by report period and filed with a lag.",
  },
  proxy_fund: {
    label: "Proxy fund",
    short: "Proxy",
    state: "partial",
    description: "A different fund's holdings stand in for the benchmark. A proxy is not the official index history.",
  },
  unavailable: {
    label: "Unavailable",
    short: "Unavailable",
    state: "not_covered",
    description: "No accessible constituent source: this subject cannot be looked through.",
  },
};

export function ledgerCounts(ledger: CoverageLedger): { tier: LedgerTier; count: number }[] {
  return LEDGER_TIERS.map((tier) => ({ tier, count: ledger.entries.filter((e) => e.tier === tier).length }));
}

/** Rows that must be reviewed before a performance or backtest uses them. */
export function backtestBlocked(ledger: CoverageLedger): LedgerRow[] {
  return ledger.entries.filter((e) => !e.usable_for_backtest);
}

export function ledgerProvenance(ledger: CoverageLedger): DataProvenance {
  const total = ledger.entries.length;
  const dates = ledger.entries.map((e) => e.checked_at).sort();
  const covered = ledger.entries.filter((e) => e.tier !== "unavailable").length;
  return {
    source: "StewardAI fund/index coverage ledger (derived from Jobs config and stored snapshots, plus owner-verified rows)",
    asOf: dates.length ? dates[dates.length - 1].slice(0, 10) : null,
    method: "One row per fund or index: where its constituents come from, and whether total-return and historical-constituent data exist.",
    state: total === 0 ? "not_covered" : covered < total ? "partial" : "verified",
    reason: total === 0 ? "No ledger rows yet: refresh the ledger to derive them from the Jobs configuration." : null,
    coverage: total > 0 ? { covered, total, unit: "funds and indices with a constituent source" } : null,
  };
}

// ---- overlap ---------------------------------------------------------------------------------

export type OverlapMatrix = {
  isins: string[];
  labels: Record<string, string>;
  cell: (a: string, b: string) => FundPairOverlap | null;
  /** The largest pair overlap, or null when there is no pair. */
  max: FundPairOverlap | null;
  mixedDates: boolean;
};

export function buildOverlapMatrix(report: FundOverlapReport): OverlapMatrix {
  const labels = Object.fromEntries(report.funds.map((f) => [f.isin, f.label]));
  const isins = report.funds.map((f) => f.isin);
  const byKey = new Map<string, FundPairOverlap>();
  for (const p of report.pairs) {
    byKey.set(`${p.fund_a}|${p.fund_b}`, p);
    byKey.set(`${p.fund_b}|${p.fund_a}`, p);
  }
  const max = report.pairs.reduce<FundPairOverlap | null>((m, p) => (m === null || p.overlap > m.overlap ? p : m), null);
  return {
    isins,
    labels,
    cell: (a, b) => (a === b ? null : (byKey.get(`${a}|${b}`) ?? null)),
    max,
    mixedDates: report.pairs.some((p) => !p.same_date),
  };
}

export function lagText(days: number | null | undefined): string {
  if (days === null || days === undefined || !Number.isFinite(days)) return "lag unknown";
  return days === 1 ? "1 day old" : `${days} days old`;
}

// ---- characteristics audit -------------------------------------------------------------------

export const AUDIT_VERDICT_META: Record<AuditVerdict, { label: string; description: string; state: DataState }> = {
  first_observation: { label: "First observation", description: "Nothing earlier to compare with.", state: "partial" },
  unchanged: { label: "Unchanged", description: "Same page shape, rules and figures as the last observation.", state: "verified" },
  normal_update: { label: "Normal update", description: "A newer as-of date with ordinary movement; no alert.", state: "verified" },
  value_change: { label: "Real value change", description: "A restated figure, a move beyond the threshold, a removed figure or an as-of date going backwards. Alerts.", state: "disputed" },
  methodology_change: { label: "Methodology change", description: "The issuer's stated capping/exclusion rules changed. Alerts.", state: "disputed" },
  parser_drift: { label: "Parser drift", description: "The payload shape changed, so the parser (not the market) is suspect. Figures are not compared and raise no value alert.", state: "unavailable" },
};

/** Newest observation per fund (rows arrive newest first; ties broken by as-of). */
export function latestAuditByFund(rows: readonly AuditRow[]): AuditRow[] {
  const best = new Map<string, AuditRow>();
  for (const r of rows) {
    const cur = best.get(r.fundKey);
    if (!cur || r.asOf > cur.asOf || (r.asOf === cur.asOf && (r.observedAt ?? "") > (cur.observedAt ?? ""))) best.set(r.fundKey, r);
  }
  return [...best.values()].sort((a, b) => a.fundKey.localeCompare(b.fundKey));
}

export function auditCounts(rows: readonly AuditRow[]): { alerts: number; drift: number } {
  return { alerts: rows.filter((r) => r.alert).length, drift: rows.filter((r) => r.needsParserReview).length };
}

export function rulesText(rules: Record<string, unknown>): string {
  const entries = Object.entries(rules).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (entries.length === 0) return "No rules stated";
  return entries.map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("; ");
}

// ---- factsheet -------------------------------------------------------------------------------

/** Newest version of each (as-of, source) pair, newest as-of first. Versions are insert-only upstream. */
export function latestFactsheetVersions(rows: readonly FactsheetRow[]): FactsheetRow[] {
  const best = new Map<string, FactsheetRow>();
  for (const r of rows) {
    const k = `${r.asOf}|${r.source}`;
    const cur = best.get(k);
    if (!cur || (r.fetchedAt ?? "") > (cur.fetchedAt ?? "")) best.set(k, r);
  }
  return [...best.values()].sort((a, b) => b.asOf.localeCompare(a.asOf) || a.source.localeCompare(b.source));
}
