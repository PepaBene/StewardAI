/*
 * Ownership channels, kept apart on purpose: Form 13F institutional holders (quarter-end,
 * filed up to 45 days later), N-PORT fund positions (report date, public ~60 days later),
 * Schedule 13D/G control stakes (percent of class, event date) and issuer-published fund
 * holdings. None of these is additive with another; nothing here sums across channels.
 */
import type { DataProvenance } from "@/lib/data-state/provenance";
import type { DataState } from "@/lib/data-state/states";

import { toNum } from "./parse";
import type { ControlStake, HoldingFundPositions, HoldingOwnership, OwnershipPeriod } from "./types";

export const CHANNEL_NOTE =
  "These channels are separate and never add up: Form 13F (managers' quarter-end positions), N-PORT (what a registered fund reports at its report date), Schedule 13D/G (beneficial-ownership stakes, percent of one class) and issuer-published fund holdings overlap each other. A fund's position is not beneficial ownership.";

export const MS_DAY = 86_400_000;

export function daysBetween(from: string, to: string): number | null {
  const a = Date.parse(from.slice(0, 10));
  const b = Date.parse(to.slice(0, 10));
  if (!Number.isFinite(a) || !Number.isFinite(b)) return null;
  return Math.round((b - a) / MS_DAY);
}

/** "2026-06-30" -> "Q2 2026" (calendar quarter of the period end). */
export function quarterLabel(period: string): string {
  const y = period.slice(0, 4);
  const m = Number(period.slice(5, 7));
  if (!/^\d{4}$/.test(y) || !(m >= 1 && m <= 12)) return period;
  return `Q${Math.ceil(m / 3)} ${y}`;
}

// ---------------------------------------------------------------------------------------------
// 13F breadth by quarter
// ---------------------------------------------------------------------------------------------

export type BreadthRow = {
  period: string;
  quarter: string;
  holders: number;
  shares: number | null;
  valueUsd: number | null;
  /** Latest / earliest effective filing of the quarter and their lags, as the API states them. */
  filed: string | null;
  lagDays: number | null;
  earliestFiled: string | null;
  earliestLagDays: number | null;
  /** The period the changes are measured against (API `previous_period`); null for the oldest loaded period. */
  previousPeriod: string | null;
  /** Changes are against the previous LOADED period; null for the oldest (missing, not zero). */
  holdersChange: number | null;
  sharesChange: number | null;
  newHolders: number | null;
  exitedHolders: number | null;
  pctOfOutstanding: number | null;
  /** Quarters between this period and the one it is compared with (>1 = a quarter is not loaded). */
  quartersSincePrevious: number | null;
};

function monthsBetween(a: string, b: string): number {
  return (Number(b.slice(0, 4)) - Number(a.slice(0, 4))) * 12 + (Number(b.slice(5, 7)) - Number(a.slice(5, 7)));
}

/** Chronological (oldest first) rows from the newest-first `periods`. */
export function breadthRows(periods: readonly OwnershipPeriod[]): BreadthRow[] {
  const sorted = [...periods].sort((a, b) => a.period.localeCompare(b.period));
  return sorted.map((p) => ({
    period: p.period,
    quarter: quarterLabel(p.period),
    holders: p.holders,
    shares: toNum(p.shares),
    valueUsd: toNum(p.value_usd),
    filed: p.latest_filed,
    lagDays: p.latest_filing_lag_days ?? null,
    earliestFiled: p.earliest_filed ?? null,
    earliestLagDays: p.earliest_filing_lag_days ?? null,
    previousPeriod: p.previous_period ?? null,
    holdersChange: p.holders_change,
    sharesChange: toNum(p.shares_change),
    newHolders: p.new_holders,
    exitedHolders: p.exited_holders,
    pctOfOutstanding: p.pct_of_shares_outstanding ?? null,
    quartersSincePrevious: p.previous_period ? Math.round(monthsBetween(p.previous_period, p.period) / 3) : null,
  }));
}

export function ownershipProvenance(own: HoldingOwnership, today: Date = new Date()): DataProvenance {
  const base = {
    source: own.source,
    method: own.basis_label ?? "Sum of 13F holders' reported shares per quarter end; managers above the 13F threshold only.",
  };
  if (!own.applicable) {
    return { ...base, asOf: null, state: "not_covered", reason: own.not_applicable_reason ?? "Form 13F does not cover this listing." };
  }
  const latest = [...own.periods].sort((a, b) => b.period.localeCompare(a.period))[0];
  if (!latest) {
    return { ...base, asOf: null, state: "unavailable", reason: "No 13F quarter is stored for this holding yet; run the 13F job." };
  }
  const age = daysBetween(latest.period, today.toISOString());
  const stale = age !== null && age > 140;
  return {
    ...base,
    asOf: latest.period,
    knownAt: latest.latest_filed,
    state: stale ? "stale" : "verified",
    reason: stale ? `Latest stored quarter ended ${age} days ago (13F is filed up to 45 days after quarter end).` : null,
  };
}

// ---------------------------------------------------------------------------------------------
// Top-holder rank flow (latest quarter vs the previous loaded quarter)
// ---------------------------------------------------------------------------------------------

export type RankFlowRow = {
  cik: string;
  name: string;
  rankNow: number;
  /** Rank among this list's holders that held shares in the previous period; null = no earlier position. */
  rankBefore: number | null;
  shares: number;
  prevShares: number | null;
  changePct: number | null;
  pctOfOutstanding: number | null;
  accession: string;
  /** Filing date and lag of this manager's filing, from the API. */
  filed: string | null;
  filingLagDays: number | null;
  /** rankBefore - rankNow: positive = moved up. */
  move: number | null;
};

export type RankFlow = {
  rows: RankFlowRow[];
  latestPeriod: string | null;
  previousPeriod: string | null;
  latestLagDays: number | null;
  previousLagDays: number | null;
  /** Always true: holders that left the latest top list are not in the data. */
  droppedNotShown: boolean;
};

export function buildRankFlow(own: HoldingOwnership): RankFlow | null {
  const holders = own.top_holders
    .map((h) => ({ h, shares: toNum(h.shares), prev: toNum(h.prev_shares) }))
    .filter((x): x is { h: (typeof own.top_holders)[number]; shares: number; prev: number | null } => x.shares !== null && x.shares > 0);
  if (holders.length === 0) return null;
  const now = [...holders].sort((a, b) => b.shares - a.shares || a.h.cik.localeCompare(b.h.cik));
  const before = holders
    .filter((x) => x.prev !== null && x.prev > 0)
    .sort((a, b) => (b.prev as number) - (a.prev as number) || a.h.cik.localeCompare(b.h.cik));
  const beforeRank = new Map(before.map((x, i) => [x.h.cik, i + 1]));
  const periods = [...own.periods].sort((a, b) => b.period.localeCompare(a.period));
  const latest = periods[0];
  const prevPeriodKey = own.top_holders_previous_period ?? own.top_holders.find((h) => h.previous_period_end)?.previous_period_end ?? null;
  const prevPeriod = prevPeriodKey ? periods.find((p) => p.period === prevPeriodKey) : undefined;
  return {
    rows: now.map((x, i) => {
      const rankBefore = beforeRank.get(x.h.cik) ?? null;
      return {
        cik: x.h.cik,
        name: x.h.manager_name ?? `CIK ${x.h.cik}`,
        rankNow: i + 1,
        rankBefore,
        shares: x.shares,
        prevShares: x.prev,
        changePct: x.h.shares_change_pct ?? null,
        pctOfOutstanding: x.h.pct_of_shares_outstanding ?? null,
        accession: x.h.accession,
        filed: x.h.filed ?? null,
        filingLagDays: x.h.filing_lag_days ?? null,
        move: rankBefore === null ? null : rankBefore - (i + 1),
      };
    }),
    latestPeriod: latest?.period ?? null,
    previousPeriod: prevPeriodKey,
    latestLagDays: latest?.latest_filing_lag_days ?? null,
    previousLagDays: prevPeriod?.latest_filing_lag_days ?? null,
    droppedNotShown: true,
  };
}

// ---------------------------------------------------------------------------------------------
// N-PORT fund positions
// ---------------------------------------------------------------------------------------------

export function nportState(c: HoldingFundPositions["coverage"]): { state: DataState; reason: string | null } {
  switch (c.state) {
    case "available":
      return { state: "verified", reason: null };
    case "stale":
      return { state: "stale", reason: "The newest stored N-PORT report is older than the expected quarterly cadence." };
    case "no_positions":
      return { state: "not_covered", reason: "No tracked fund reports a position in this holding. This is not evidence that no fund holds it." };
    case "no_funds_loaded":
      return { state: "unavailable", reason: "No N-PORT fund filings are loaded yet; run the N-PORT job." };
  }
}

/** "Showing N of M" note from the API's own count; null when every matching position is shown. */
export function positionsNote(data: Pick<HoldingFundPositions, "positions" | "matching_positions" | "truncated">): string | null {
  if (!data.truncated) return null;
  return `Showing ${data.positions.length} of ${data.matching_positions} fund positions that match this holding across the loaded funds.`;
}

// ---------------------------------------------------------------------------------------------
// Schedule 13D/G
// ---------------------------------------------------------------------------------------------

export type StakeView = {
  stake: ControlStake;
  percent: number | null;
  shares: number | null;
  /** Percent as filed vs the cross-check: a flagged disagreement, never silently replaced. */
  checkLabel: string;
  /** More than one reporting person, or a member of a group: the percent is one person's figure. */
  isGroup: boolean;
  intentChanged: boolean;
  ownersCount: number;
};

/** What the filing-level percent is, per the backend: the largest single reporting-person figure. */
export function stakePercentNote(v: Pick<StakeView, "isGroup" | "ownersCount">): string | null {
  if (!v.isGroup) return null;
  return `${v.ownersCount > 1 ? `${v.ownersCount} reporting persons` : "Member of a group"}; the percent is the largest single reporting person's figure. Members report overlapping holdings and are never summed, so the group's total is at least this.`;
}

export function stakeViews(stakes: readonly ControlStake[]): StakeView[] {
  return [...stakes]
    .sort((a, b) => b.filed.localeCompare(a.filed) || b.accession.localeCompare(a.accession))
    .map((s) => ({
      stake: s,
      percent: toNum(s.stake_percent),
      shares: toNum(s.stake_shares),
      checkLabel:
        s.reconciliation.percent_check === "consistent"
          ? "Percent consistent with shares / class outstanding"
          : s.reconciliation.percent_check === "inconsistent"
            ? "Percent does NOT match shares / class outstanding"
            : "Percent not checkable against the class",
      isGroup: s.holders.length > 1 || s.holders.some((h) => h.member_of_group === true),
      intentChanged: s.changes.some((c) => /intent|purpose/i.test(c.change_type)),
      ownersCount: s.holders.length,
    }));
}
