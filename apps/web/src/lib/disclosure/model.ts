import type { components } from "@/lib/api/schema";

/*
 * Public-disclosure evidence browser: types and pure logic. Types come from the generated API
 * schema, so the fixtures (`fixtures.ts`) and the live endpoints share one contract and the
 * screen never needs to know which one it is showing.
 *
 * The rule the screen exists to make visible: a row is known at a cutoff only if its `known_at`
 * is on or before it. `filed`, `accepted`, `published`, `known`, `first seen` and `retrieved` are
 * different moments and are always shown apart; a missing one is explained, never blank.
 */

type S = components["schemas"];
export type Filing = S["DocumentVersionRow"];
export type Observation = S["ObservationRow"];
export type Claim = S["ClaimRow"];
export type Coverage = S["CoverageRow"];
export type Snapshot = S["Snapshot"];
export type SourceRow = S["SourceRow"];
export type ReviewRow = S["ReviewRow"];
export type IssuerRow = S["IssuerRow"];
export type Provenance = S["Provenance"];
export type SpanText = S["SpanText"];
export type VersionRef = S["VersionRef"];

// ------------------------------------------------------------------------------ form families
export type FormFamily = "annual" | "interim" | "current" | "ownership" | "registration" | "other";

export const FAMILY_LABEL: Record<FormFamily, string> = {
  annual: "Annual reports",
  interim: "Quarterly reports",
  current: "Current reports",
  ownership: "Ownership and insiders",
  registration: "Registration and proxy",
  other: "Other documents",
};

/** Display order of lanes on the rail, top to bottom. */
export const FAMILY_ORDER: readonly FormFamily[] = ["annual", "interim", "current", "ownership", "registration", "other"];

export function formFamily(form: string | null | undefined): FormFamily {
  const f = (form ?? "").toUpperCase().replace(/\/A$/, "");
  if (["10-K", "10-KT", "20-F", "40-F", "ANNUAL REPORT", "ARS"].includes(f)) return "annual";
  if (["10-Q", "10-QT", "INTERIM REPORT", "HALF-YEAR REPORT"].includes(f)) return "interim";
  if (["8-K", "6-K", "EX-99.1", "EX-99"].includes(f) || f.startsWith("EX-")) return "current";
  if (f.startsWith("SC 13") || f.startsWith("13") || ["3", "4", "5", "TR-1", "PDMR"].includes(f)) return "ownership";
  if (f.startsWith("S-") || f.startsWith("F-") || f.startsWith("424") || f.startsWith("DEF") || f === "PROXY") return "registration";
  return "other";
}

export const isAmendment = (f: Filing) => (f.form ?? "").toUpperCase().endsWith("/A");

// ----------------------------------------------------------------------- coverage state machine
export type CoverageState =
  | "verified"
  | "under_review"
  | "not_disclosed"
  | "not_covered"
  | "paid_only"
  | "authentication_failed"
  | "retrieval_failed"
  | "parse_failed"
  | "unresolved_identity"
  | "stale";

export type CoverageTone = "verified" | "unconfirmed" | "neutral" | "warning";

export type CoverageStateInfo = {
  label: string;
  tone: CoverageTone;
  /** What the owner can do or conclude; never implies a zero value. */
  help: string;
};

export const COVERAGE_STATES: Record<CoverageState, CoverageStateInfo> = {
  verified: { label: "Verified", tone: "verified", help: "A reviewer confirmed the value against the filing." },
  under_review: { label: "Under review", tone: "unconfirmed", help: "A machine reading waits for a reviewer. Not yet a fact." },
  not_disclosed: { label: "Not disclosed", tone: "neutral", help: "The filing does not report it. This is not zero." },
  not_covered: { label: "Not covered", tone: "neutral", help: "No source we can read covers this issuer and period." },
  paid_only: { label: "Paid source only", tone: "neutral", help: "The only source found needs a paid plan. Nothing is bought; the gap stays visible." },
  authentication_failed: { label: "Access refused", tone: "warning", help: "The source refused our credentials or declared identity. Check the key or User-Agent." },
  retrieval_failed: { label: "Fetch failed", tone: "warning", help: "The source did not answer. The last good value, if any, is shown as stale." },
  parse_failed: { label: "Could not read", tone: "warning", help: "The document was fetched but could not be read. It is quarantined for review." },
  unresolved_identity: { label: "Company not matched", tone: "warning", help: "The filing names a company we could not tie to a known issuer." },
  stale: { label: "Out of date", tone: "neutral", help: "Newer filings are expected but have not been read yet." },
};

export function coverageInfo(state: string): CoverageStateInfo {
  return COVERAGE_STATES[state as CoverageState] ?? { label: state, tone: "neutral", help: "Unknown state." };
}

export const TONE_CLASS: Record<CoverageTone, string> = {
  verified: "border-verified/40 bg-verified-subtle text-verified",
  unconfirmed: "border-unconfirmed/40 bg-unconfirmed-subtle text-unconfirmed",
  neutral: "border-border bg-neutral-signal-subtle text-neutral-signal",
  warning: "border-warning-signal/40 bg-warning-signal-subtle text-warning-signal",
};

// -------------------------------------------------------------------------------------- dates
export type DateRow = { key: string; label: string; value: string | null; note: string | null };

/**
 * The six moments of a filing, each on its own row. A null date carries its reason: from the
 * source's own `date_notes` when it gave one, else a plain statement. Nothing is blank.
 */
export function dateRows(f: Filing): DateRow[] {
  const notes = f.date_notes ?? {};
  const row = (key: string, label: string, value: string | null, fallback: string): DateRow => ({
    key,
    label,
    value,
    note: value ? null : (notes[key] ?? fallback),
  });
  return [
    row("filed_at", "Filed", f.filed_at, "The source gives no filing date."),
    row("accepted_at", "Accepted", f.accepted_at, "The source gives no acceptance time."),
    row("published_at", "Published", f.published_at, "The source gives no separate publication time."),
    { key: "known_at", label: "Known from", value: f.known_at, note: "The moment this version counts as known. Nothing earlier may use it." },
    row("first_seen_at", "First seen here", f.first_seen_at, "Not recorded."),
    row("retrieved_at", "Last retrieved", f.retrieved_at, "Not recorded."),
  ];
}

export type FilingStatus = "current" | "superseded" | "amendment" | "quarantined";

export function filingStatus(f: Filing): FilingStatus {
  if (f.quarantined) return "quarantined";
  if (isAmendment(f)) return "amendment";
  if (f.superseded_by_id != null || !f.is_current) return "superseded";
  return "current";
}

// -------------------------------------------------------------------------- as-of (client side)
const ms = (iso: string | null | undefined): number => (iso ? Date.parse(iso) : Number.NaN);

export function isKnownAt(iso: string, cutoffMs: number): boolean {
  const t = ms(iso);
  return Number.isFinite(t) && t <= cutoffMs;
}

/**
 * Filings visible at a cutoff. Links to a version that was itself not yet known are removed, and
 * `is_current` is recomputed, so a replay never learns from the future that a filing was amended.
 * Newest first.
 */
export function filingsAt(all: readonly Filing[], cutoffMs: number): Filing[] {
  const knownIds = new Set(all.filter((f) => isKnownAt(f.known_at, cutoffMs)).map((f) => f.version_id));
  return all
    .filter((f) => knownIds.has(f.version_id))
    .map((f) => ({
      ...f,
      amends_version_id: f.amends_version_id != null && knownIds.has(f.amends_version_id) ? f.amends_version_id : null,
      superseded_by_id: f.superseded_by_id != null && knownIds.has(f.superseded_by_id) ? f.superseded_by_id : null,
      is_current: !(f.superseded_by_id != null && knownIds.has(f.superseded_by_id)),
    }))
    .sort((a, b) => ms(b.known_at) - ms(a.known_at) || b.version_id - a.version_id);
}

/** True when a row (observation or claim) would be hidden at the cutoff by a known successor. */
export function latestKnown<T extends { id: number; known_at: string }>(
  rows: readonly T[],
  cutoffMs: number,
  predecessorOf: (row: T) => number | null,
): T[] {
  const visible = rows.filter((r) => isKnownAt(r.known_at, cutoffMs));
  const replaced = new Set(visible.map(predecessorOf).filter((id): id is number => id != null));
  return visible.filter((r) => !replaced.has(r.id));
}

export const observationsAt = (all: readonly Observation[], cutoffMs: number) =>
  latestKnown(all, cutoffMs, (o) => o.supersedes_id).filter((o) => o.review_state !== "rejected" && o.review_state !== "superseded");

export const claimsAt = (all: readonly Claim[], cutoffMs: number) =>
  latestKnown(all, cutoffMs, (c) => c.supersedes_claim_id).filter((c) => c.review_state !== "rejected" && c.review_state !== "superseded" && c.polarity !== "superseded");

// ---------------------------------------------------------------------------------- formatting
const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const STAMP = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC", timeZoneName: "short" });

export function formatDay(iso: string | null | undefined): string {
  const t = ms(iso);
  return Number.isFinite(t) ? DAY.format(new Date(t)) : "—";
}

export function formatStamp(iso: string | null | undefined): string {
  const t = ms(iso);
  return Number.isFinite(t) ? STAMP.format(new Date(t)) : "—";
}

/** A reported value with its scale and unit, exactly as stored: "1,250,500,000 USD". */
export function formatValue(o: Pick<Observation, "value" | "value_state" | "unit" | "text_value" | "currency">): string {
  if (o.value_state === "not_disclosed") return "Not disclosed";
  if (o.value_state === "null" || (o.value == null && o.text_value == null)) return "No value reported";
  if (o.value == null) return o.text_value ?? "";
  const n = Number(o.value);
  const text = Number.isFinite(n) ? new Intl.NumberFormat("en-GB", { maximumFractionDigits: 4 }).format(n) : o.value;
  return `${text} ${o.currency ?? o.unit}`;
}

/** Local date input value ("2026-03-15") to the end of that UTC day, the latest moment of the day. */
export function endOfDayMs(day: string): number {
  const t = Date.parse(`${day}T23:59:59Z`);
  return Number.isFinite(t) ? t : Number.NaN;
}

export function dayOf(ms_: number): string {
  return new Date(ms_).toISOString().slice(0, 10);
}
