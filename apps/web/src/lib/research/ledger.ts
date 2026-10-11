import type { ResearchBody, Source } from "@/lib/api/client";

/*
 * The claim ledger of a research report: each sentence typed, with a confidence label and
 * its reason, a review state the owner can set, the strategy checks, the balance of the
 * argument and the coverage notices. Old stored reports have no ledger (null).
 */

export type ClaimLedger = NonNullable<ResearchBody["ledger"]>;
export type LedgerClaim = ClaimLedger["claims"][number];
export type ReviewState = LedgerClaim["review"];
/** The states the review route accepts. */
export type OwnerReviewState = "owner_confirmed" | "owner_rejected" | "unreviewed";

export const CLAIM_TYPE_LABELS: Record<LedgerClaim["type"], string> = {
  fact: "Fact",
  calculation: "Calculation",
  interpretation: "Interpretation",
  strategy_assessment: "Strategy assessment",
  forecast: "Forecast",
  unknown: "Unclassified",
};

export function claimTypeLabel(type: string): string {
  return CLAIM_TYPE_LABELS[type as LedgerClaim["type"]] ?? type.replaceAll("_", " ");
}

export const CONFIDENCE_LABELS: Record<LedgerClaim["confidence"], string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};

export function confidenceLabel(level: string): string {
  return CONFIDENCE_LABELS[level as LedgerClaim["confidence"]] ?? level;
}

export const REVIEW_LABELS: Record<ReviewState, string> = {
  unreviewed: "Not reviewed",
  machine_checked: "Checked by the claim verifier",
  flagged: "Flagged by the claim verifier",
  owner_confirmed: "Confirmed by you",
  owner_rejected: "Rejected by you",
};

export function reviewLabel(state: string): string {
  return REVIEW_LABELS[state as ReviewState] ?? state.replaceAll("_", " ");
}

/** The state a confirm or reject press asks for: pressing the active choice again clears it. */
export function nextReviewState(current: ReviewState, action: "confirm" | "reject"): OwnerReviewState {
  const target = action === "confirm" ? "owner_confirmed" : "owner_rejected";
  return current === target ? "unreviewed" : target;
}

export type ClaimGroup = { sectionId: string; title: string; claims: LedgerClaim[] };

/** Claims grouped by the report's sections, in section order; claims of unknown sections last. */
export function groupClaimsBySection(
  claims: readonly LedgerClaim[],
  sections: readonly { id: string; title: string }[],
): ClaimGroup[] {
  const groups: ClaimGroup[] = sections.map((s) => ({ sectionId: s.id, title: s.title || s.id, claims: [] }));
  for (const claim of claims) {
    let group = groups.find((g) => g.sectionId === claim.section_id);
    if (!group) {
      group = { sectionId: claim.section_id, title: claim.section_id.replaceAll("_", " "), claims: [] };
      groups.push(group);
    }
    group.claims.push(claim);
  }
  return groups.filter((g) => g.claims.length > 0).map((g) => ({ ...g, claims: [...g.claims].sort((a, b) => a.index - b.index) }));
}

/** Counts by claim type or confidence, as labelled pairs (largest first). */
export function tally(by: Record<string, number> | null | undefined, label: (key: string) => string): { key: string; label: string; count: number }[] {
  return Object.entries(by ?? {})
    .filter(([, n]) => n > 0)
    .map(([key, count]) => ({ key, label: label(key), count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));
}

export function staleClaimCount(claims: readonly Pick<LedgerClaim, "stale">[]): number {
  return claims.filter((c) => c.stale).length;
}

export const STRATEGY_STATUS_LABELS: Record<ClaimLedger["strategy"][number]["status"], string> = {
  supports: "Supports",
  challenges: "Challenges",
  mixed: "Mixed",
  unknown: "Unknown",
};

export function strategyStatusLabel(status: string): string {
  return STRATEGY_STATUS_LABELS[status as keyof typeof STRATEGY_STATUS_LABELS] ?? status;
}

/** The rule engine's check of a strategy rule against the latest snapshot (formal compliance). */
export type RuleCompliance = {
  status: "pass" | "breach" | "warn" | "unknown";
  measured?: number | null;
  limit?: number | null;
  operator?: string | null;
  unit?: "weight" | "count";
  as_of?: string | null;
  reason: string;
  scope?: "ticker" | "portfolio";
};

export const COMPLIANCE_LABELS: Record<RuleCompliance["status"], string> = {
  pass: "Pass",
  breach: "Breach",
  warn: "Warning: close to the limit",
  unknown: "Not checked",
};

/** The compliance of a strategy check, or null for a thesis KPI and for reports stored before it existed. */
export function complianceOf(check: unknown): RuleCompliance | null {
  return (check as { compliance?: RuleCompliance | null } | null | undefined)?.compliance ?? null;
}

function formatMeasure(value: number, unit: RuleCompliance["unit"]): string {
  return unit === "count" ? String(Math.round(value)) : `${(value * 100).toFixed(2)} %`;
}

/** "Measured 8.38 % against <= 9.00 % as of 2026-10-10", or the reason it was not checked. */
export function complianceLine(c: RuleCompliance): string {
  if (c.status === "unknown" || c.measured === null || c.measured === undefined) return c.reason;
  const unit = c.unit ?? "weight";
  const limit = c.limit === null || c.limit === undefined ? "" : ` against ${c.operator ?? ""} ${formatMeasure(c.limit, unit)}`.replace("  ", " ");
  const date = c.as_of ? ` as of ${c.as_of}` : "";
  return `Measured ${formatMeasure(c.measured, unit)}${limit}${date}${c.scope === "portfolio" ? " (whole portfolio)" : ""}`;
}

export function strategyKindLabel(kind: string): string {
  return kind === "thesis_kpi" ? "Thesis KPI" : kind === "strategy_rule" ? "Strategy rule" : kind.replaceAll("_", " ");
}

/** What the balance check found missing or not asked for, as sentences. Empty when the argument is balanced. */
export function balanceLines(balance: ClaimLedger["balance"] | null | undefined): string[] {
  if (!balance) return [];
  const present = [
    balance.has_support ? "support" : null,
    balance.has_challenge ? "challenge" : null,
    balance.has_unknowns ? "unknowns" : null,
    balance.has_scenarios ? "scenarios" : null,
  ].filter((p): p is string => p !== null);
  const lines: string[] = [];
  if (present.length > 0) lines.push(`Covered: ${present.join(", ")}.`);
  if (balance.missing.length > 0) lines.push(`Missing: ${balance.missing.join(", ")}.`);
  if (balance.not_requested.length > 0) lines.push(`Not requested by this profile: ${balance.not_requested.join(", ")}.`);
  return lines;
}

/** As-of and coverage notices: the data window, stale and undated sources, and the run's own notices. */
export function coverageNotices(coverage: ClaimLedger["coverage"] | null | undefined): string[] {
  if (!coverage) return [];
  const out: string[] = [];
  if (coverage.data_from || coverage.data_to) {
    out.push(`Data from ${coverage.data_from ?? "unknown"} to ${coverage.data_to ?? "unknown"}.`);
  }
  const stale = coverage.stale_sources ?? [];
  if (stale.length > 0) out.push(`Stale sources: ${stale.join(", ")}.`);
  const undated = coverage.undated_sources ?? [];
  if (undated.length > 0) out.push(`Sources without a date: ${undated.join(", ")}.`);
  return [...out, ...(coverage.notices ?? [])];
}

/** Flagged source ids resolved to their titles (the id alone when the source is not listed). */
export function flaggedSourceWarnings(
  flagged: readonly string[] | null | undefined,
  sources: readonly Pick<Source, "id" | "title">[],
): { id: string; title: string }[] {
  return (flagged ?? []).map((id) => ({ id, title: sources.find((s) => s.id === id)?.title ?? id }));
}
