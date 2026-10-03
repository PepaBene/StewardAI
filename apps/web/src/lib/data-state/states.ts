/*
 * The shared availability vocabulary of the free-data-sources plan (Phase 0 frontend). Every
 * view that consumes a new provider's data speaks these nine states, so "no data" never
 * reads as "zero" and a failed refresh never silently replaces a verified value.
 *
 * The backend speaks a slightly different vocabulary (`available`, `temporarily_failed`,
 * `vintage_unavailable`, `missing`, ...). EVERY view converts a backend string with
 * `toDataState` below; nothing else maps backend strings to these nine.
 */

export const DATA_STATES = [
  "verified",
  "stale",
  "partial",
  "not_covered",
  "paid_only",
  "quota_exhausted",
  "access_unverified",
  "disputed",
  "unavailable",
] as const;

export type DataState = (typeof DATA_STATES)[number];

/** Which palette family carries the state (always paired with an icon and the label). */
export type StateTone = "verified" | "warning" | "disputed" | "neutral" | "info";

export type DataStateMeta = {
  label: string;
  /** One sentence: what the owner should understand when they see this state. */
  description: string;
  tone: StateTone;
  /** Does a figure exist that may be drawn (possibly with a notice)? */
  showsValue: boolean;
  /** What the owner can do about it, when anything. */
  nextStep: string | null;
};

export const DATA_STATE_META: Record<DataState, DataStateMeta> = {
  verified: {
    label: "Verified",
    description: "A current observation from an accessible source, reconciled against its source of record.",
    tone: "verified",
    showsValue: true,
    nextStep: null,
  },
  stale: {
    label: "Stale",
    description: "The last verified value is shown, but it is older than this dataset's refresh interval.",
    tone: "warning",
    showsValue: true,
    nextStep: "Check the last job run for this source.",
  },
  partial: {
    label: "Partial coverage",
    description: "Only part of the expected universe or period range has data; the denominator is shown beside every figure.",
    tone: "warning",
    showsValue: true,
    nextStep: "Open the coverage view to see which entities are missing.",
  },
  not_covered: {
    label: "Not covered",
    description: "The source does not cover this market, entity or period. This is a measured gap, not a zero.",
    tone: "neutral",
    showsValue: false,
    nextStep: null,
  },
  paid_only: {
    label: "Paid only",
    description: "The provider returned a premium-tier denial for this endpoint. It is never retried daily and never replaced by a paid call.",
    tone: "neutral",
    showsValue: false,
    nextStep: null,
  },
  quota_exhausted: {
    label: "Quota exhausted",
    description: "The free allowance for this window is used up. Collection resumes at the next reset; nothing is bought.",
    tone: "warning",
    showsValue: false,
    nextStep: "Wait for the reset shown in the budget view.",
  },
  access_unverified: {
    label: "Access unverified",
    description: "A free-tier route has not been confirmed with an actual key for this endpoint, so no data is collected from it.",
    tone: "info",
    showsValue: false,
    nextStep: "Run the Phase 0 access probe for this endpoint.",
  },
  disputed: {
    label: "Disputed",
    description: "Sources disagree beyond the metric's tolerance. Both values are kept; neither has been silently chosen.",
    tone: "disputed",
    showsValue: true,
    nextStep: "Review the conflict.",
  },
  unavailable: {
    label: "Temporarily unavailable",
    description: "The last request failed (network, 5xx or schema drift). The last verified value is kept until the source recovers.",
    tone: "warning",
    showsValue: false,
    nextStep: "See the failed job in Activity.",
  },
};

export function isDataState(value: string): value is DataState {
  return (DATA_STATES as readonly string[]).includes(value);
}

/**
 * Every state string the backend can emit (coverage_status.state, provider_access.state,
 * provider freshness, macro read models) and the DataState it is shown as.
 *
 * - `available` -> verified; `temporarily_failed` -> unavailable (backend AccessState).
 * - `vintage_unavailable` (macro as-of reads: the release history was not archived before
 *   collection began) -> not_covered: a measured gap for that as-of date, not a failure.
 * - `missing` (plan wording of an absent entry) -> not_covered.
 * - freshness values of /sources/access: `recent` -> verified, `stale` -> stale,
 *   `never_succeeded` -> access_unverified (no successful call is on record).
 * `stale` and `partial` are derived states in the backend (freshness, coverage ratio), never
 * stored in a ledger; they pass through when a view derives them.
 */
export const BACKEND_STATE_MAP: Readonly<Record<string, DataState>> = {
  available: "verified",
  verified: "verified",
  recent: "verified",
  stale: "stale",
  partial: "partial",
  not_covered: "not_covered",
  missing: "not_covered",
  vintage_unavailable: "not_covered",
  paid_only: "paid_only",
  quota_exhausted: "quota_exhausted",
  access_unverified: "access_unverified",
  never_succeeded: "access_unverified",
  disputed: "disputed",
  unavailable: "unavailable",
  temporarily_failed: "unavailable",
};

/** The DataState for a backend string, or null when the string is not part of the vocabulary. */
export function parseDataState(value: string | null | undefined): DataState | null {
  if (typeof value !== "string") return null;
  return Object.hasOwn(BACKEND_STATE_MAP, value) ? BACKEND_STATE_MAP[value] : null;
}

/**
 * The single adapter from a backend state string to the nine-state vocabulary. An unknown or
 * missing string becomes "unavailable" (never "verified", never a zero): the UI then says the
 * state could not be determined instead of implying data exists.
 */
export function toDataState(value: string | null | undefined): DataState {
  return parseDataState(value) ?? "unavailable";
}

/** Tone classes: written out in full so Tailwind finds them. */
export const STATE_TONE_CLASSES: Record<StateTone, { chip: string; dot: string }> = {
  verified: { chip: "border-verified/40 bg-verified-subtle text-text", dot: "bg-verified" },
  warning: { chip: "border-warning-signal/40 bg-warning-signal-subtle text-text", dot: "bg-warning-signal" },
  disputed: { chip: "border-disputed/40 bg-disputed-subtle text-text", dot: "bg-disputed" },
  neutral: { chip: "border-border bg-neutral-signal-subtle text-text", dot: "bg-neutral-signal" },
  info: { chip: "border-info-signal/40 bg-info-signal-subtle text-text", dot: "bg-info-signal" },
};

/** How a view should render a value given its state and whether a last-good value exists. */
export type DisplayDecision =
  /** Draw the value as usual. */
  | "value"
  /** Draw the value with the state's notice next to it (stale, partial, disputed). */
  | "value_with_notice"
  /** A refresh failed or quota is spent but a last-good value exists: keep it, flag it. */
  | "last_good_with_notice"
  /** Nothing to draw: show the state placeholder, never a zero. */
  | "placeholder";

export function displayDecision(state: DataState, hasValue: boolean): DisplayDecision {
  if (state === "verified") return hasValue ? "value" : "placeholder";
  if (DATA_STATE_META[state].showsValue) return hasValue ? "value_with_notice" : "placeholder";
  // Non-showing states keep the previous verified figure when one exists (D1 exit check:
  // a provider 403/429 cannot erase the last good observation) — except a structural
  // denial, where no value could ever have come from this endpoint.
  if (state === "unavailable" || state === "quota_exhausted") return hasValue ? "last_good_with_notice" : "placeholder";
  return "placeholder";
}
