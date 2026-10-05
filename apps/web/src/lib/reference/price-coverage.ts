/**
 * Pure helpers for the price coverage calendar and the corporate-action coverage panel. The
 * exchange-session and coverage responses are untyped, so everything is parsed defensively.
 */
import type { components } from "@/lib/api/schema";

import { isRec, text } from "./holding-charts";

type ExpectedSessionsResponse = components["schemas"]["ExpectedSessionsResponse"];
type ActionCoverageResponse = components["schemas"]["ActionCoverageResponse"];

const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

// ---------------------------------------------------------------- session strip

export type SessionStateName = "observed" | "missing" | "disagrees";
export type SessionCell = { session: string; state: SessionStateName };

/** Oldest-first, valid dates only; an unknown state is treated as missing (never as observed). */
export function parseSessions(raw: unknown): SessionCell[] {
  return arr(raw)
    .filter(isRec)
    .map((r): SessionCell | null => {
      const session = text(r.session);
      if (!session || !ISO_DATE.test(session)) return null;
      const s = text(r.state);
      return { session, state: s === "observed" || s === "disagrees" ? s : "missing" };
    })
    .filter((c): c is SessionCell => c !== null)
    .sort((a, b) => a.session.localeCompare(b.session));
}

export type SessionCounts = { total: number; observed: number; missing: number; disagrees: number };

export function sessionCounts(cells: readonly SessionCell[]): SessionCounts {
  const c: SessionCounts = { total: cells.length, observed: 0, missing: 0, disagrees: 0 };
  for (const x of cells) c[x.state] += 1;
  return c;
}

/** Monday = 0 .. Sunday = 6 for an ISO date. */
export function weekdayRow(date: string): number {
  const [y, m, d] = date.split("-").map(Number);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
}

function weekStart(date: string): string {
  const [y, m, d] = date.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d - weekdayRow(date)));
  return t.toISOString().slice(0, 10);
}

export type StripLayout = {
  /** Number of week columns, oldest first. */
  columns: number;
  /** 5 for a Monday-to-Friday calendar, 7 if any session falls on a weekend. */
  rows: number;
  /** Each cell's grid position (1-based) so empty slots stay empty. */
  cells: (SessionCell & { column: number; row: number })[];
};

/** Week-aligned grid: one column per calendar week, one row per weekday. */
export function stripLayout(cells: readonly SessionCell[]): StripLayout {
  if (cells.length === 0) return { columns: 0, rows: 5, cells: [] };
  const starts: string[] = [];
  const placed = cells.map((c) => {
    const ws = weekStart(c.session);
    if (!starts.includes(ws)) starts.push(ws);
    return { ...c, ws, row: weekdayRow(c.session) + 1 };
  });
  const rows = placed.some((p) => p.row > 5) ? 7 : 5;
  return { columns: starts.length, rows, cells: placed.map(({ ws, ...p }) => ({ ...p, column: starts.indexOf(ws) + 1 })) };
}

// ---------------------------------------------------------------- exchange sessions

export type SpecialDay = { date: string; label: string };
export type ExpectedInfo = {
  basis: string | null;
  basisLabel: string;
  count: number | null;
  /** How many sessions the owner confirmed in the window (the endpoint gives a count only). */
  confirmedDays: number | null;
  /** Days to mark as special. The endpoint does not say which days were confirmed, so this stays empty. */
  special: SpecialDay[];
};

export function basisLabel(basis: string | null): string {
  if (!basis) return "Calendar basis not stated";
  if (/confirm/i.test(basis) && !/weekday/i.test(basis)) return "Confirmed sessions";
  if (/weekday/i.test(basis) || /config/i.test(basis)) return "Weekdays less configured closures";
  return basis.replace(/_/g, " ");
}

/** `confirmed_days` is a count of confirmed sessions, not a list of dates. */
export function parseExpected(raw: ExpectedSessionsResponse): ExpectedInfo {
  const basis = raw.basis || null;
  return { basis, basisLabel: basisLabel(basis), count: raw.count, confirmedDays: raw.confirmed_days, special: [] };
}

// ---------------------------------------------------------------- corporate-action coverage

export type CoverageEntry = {
  key: string;
  provider: string;
  state: string;
  stateLabel: string;
  /** True only when the provider states the coverage is complete through `latestPeriod`. */
  covered: boolean;
  latestPeriod: string | null;
  checkedAt: string | null;
  reason: string | null;
  ownerVerified: boolean;
};

// coverage_status.state is one of available, not_covered, paid_only, access_unverified,
// quota_exhausted, temporarily_failed, disputed; only `available` means the data is there.
const COVERED_STATES = new Set(["available"]);

export function parseCoverage(raw: ActionCoverageResponse | null): CoverageEntry[] {
  return (raw?.entries ?? [])
    .map((e, i): CoverageEntry => {
      const state = e.state || "unknown";
      const covered = COVERED_STATES.has(state.toLowerCase());
      return {
        key: `${e.provider || i}:${e.checked_at || i}`,
        provider: e.provider || "Unknown provider",
        state,
        stateLabel: covered ? "Covered" : state.replace(/_/g, " "),
        covered,
        latestPeriod: e.latest_period ?? null,
        checkedAt: e.checked_at || null,
        reason: e.reason ?? null,
        ownerVerified: e.owner_verified,
      };
    })
    .sort((a, b) => Number(b.ownerVerified) - Number(a.ownerVerified) || (b.checkedAt ?? "").localeCompare(a.checkedAt ?? ""));
}

export type CoverageDraft = { through: string; sourceUrl: string; reason: string };
export type CoverageErrors = Partial<Record<keyof CoverageDraft, string>>;

export const COVERAGE_LIMITS = { sourceUrlMin: 8, reasonMin: 10 } as const;

/** Mirrors the API limits (source_url >= 8 characters, reason >= 10) and rejects future dates. */
export function validateCoverage(draft: CoverageDraft, today: string): CoverageErrors {
  const errors: CoverageErrors = {};
  const through = draft.through.trim();
  if (!through) errors.through = "Enter the date coverage runs through.";
  else if (!ISO_DATE.test(through) || Number.isNaN(Date.parse(`${through}T00:00:00Z`))) errors.through = "Use a valid date (YYYY-MM-DD).";
  else if (through > today) errors.through = "Coverage cannot run through a future date.";
  const url = draft.sourceUrl.trim();
  if (url.length < COVERAGE_LIMITS.sourceUrlMin) errors.sourceUrl = `Enter the source you checked (at least ${COVERAGE_LIMITS.sourceUrlMin} characters).`;
  const reason = draft.reason.trim();
  if (reason.length < COVERAGE_LIMITS.reasonMin) errors.reason = `Say what you verified (at least ${COVERAGE_LIMITS.reasonMin} characters).`;
  return errors;
}
