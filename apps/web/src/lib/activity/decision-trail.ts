import type { components } from "@/lib/api/schema";

/*
 * Decision trail of the free-source alert rules (GET /alerts/free-source-trail): one row per
 * decision, raised or not. Pure display rules: outcome wording, evidence fields, paging.
 */

export type TrailRow = components["schemas"]["TrailRow"];
export type TrailOut = components["schemas"]["TrailOut"];

export const TRAIL_RULES = ["credit_stress", "trade_exposure", "patent_status", "company_graph"] as const;
export type TrailRule = (typeof TRAIL_RULES)[number];
export const TRAIL_RULE_LABELS: Record<TrailRule, string> = {
  credit_stress: "Credit stress",
  trade_exposure: "Trade exposure",
  patent_status: "Patent / legal status",
  company_graph: "Company graph",
};

export const TRAIL_OUTCOMES = [
  "raised",
  "throttled",
  "revision_ignored",
  "baseline",
  "not_material",
  "no_baseline",
  "persisting",
  "review_needed",
  "below_severity",
  "blocked_by_gate",
] as const;
export type TrailOutcome = (typeof TRAIL_OUTCOMES)[number];

export type OutcomeKind = "alerted" | "suppressed" | "informational" | "provisional";

export const OUTCOME_META: Record<TrailOutcome, { label: string; kind: OutcomeKind; meaning: string }> = {
  raised: { label: "Raised", kind: "alerted", meaning: "An alert was created." },
  throttled: { label: "Throttled", kind: "suppressed", meaning: "An alert for the same rule and scope was raised within the throttle window." },
  revision_ignored: { label: "Revision ignored", kind: "suppressed", meaning: "A later vintage of an already evaluated observation; not alerted again." },
  baseline: { label: "Baseline", kind: "informational", meaning: "First observation recorded as the baseline; nothing to compare with yet." },
  not_material: { label: "Not material", kind: "informational", meaning: "The change was below the configured threshold." },
  no_baseline: { label: "No baseline", kind: "informational", meaning: "No earlier observation exists to compare with." },
  persisting: { label: "Persisting", kind: "suppressed", meaning: "The condition was already alerted and still holds." },
  review_needed: { label: "Review needed", kind: "informational", meaning: "Suggested or disputed; listed for your review, not alerted." },
  below_severity: { label: "Below severity", kind: "suppressed", meaning: "The severity configured for this rule is below the alerting level." },
  blocked_by_gate: { label: "Blocked by gate", kind: "provisional", meaning: "A verification gate was closed; the next run replaces this row once it opens." },
};

export function isTrailOutcome(value: string): value is TrailOutcome {
  return (TRAIL_OUTCOMES as readonly string[]).includes(value);
}

export function outcomeMeta(outcome: string) {
  return isTrailOutcome(outcome) ? OUTCOME_META[outcome] : { label: outcome.replace(/_/g, " "), kind: "informational" as const, meaning: "An outcome this view does not know." };
}

export function ruleLabel(rule: string): string {
  return (TRAIL_RULE_LABELS as Record<string, string>)[rule] ?? rule.replace(/_/g, " ");
}

export type EvidenceField = { key: string; text: string; href?: string };

const SCALAR = (v: unknown): v is string | number | boolean => ["string", "number", "boolean"].includes(typeof v);

/**
 * Evidence as readable fields: scalars as text, the source URL as a link (http/https only),
 * the verification gate as "name: allowed|denied (reason)". Nested values other than the gate
 * are summarised by their key; null fields are listed as "none".
 */
export function evidenceFields(evidence: Record<string, unknown>): EvidenceField[] {
  const out: EvidenceField[] = [];
  for (const [key, value] of Object.entries(evidence)) {
    if (key === "gate" && value && typeof value === "object") {
      const g = value as { name?: unknown; allowed?: unknown; reason?: unknown };
      out.push({
        key: "gate",
        text: `${String(g.name ?? "unnamed")}: ${g.allowed === true ? "open" : "closed"}${typeof g.reason === "string" ? ` (${g.reason})` : ""}`,
      });
    } else if (key === "source_url" && typeof value === "string") {
      out.push(/^https?:\/\//.test(value) ? { key, text: "source", href: value } : { key, text: value });
    } else if (SCALAR(value)) {
      out.push({ key, text: String(value) });
    } else if (value === null || value === undefined) {
      out.push({ key, text: "none" });
    } else {
      out.push({ key, text: Array.isArray(value) ? `${value.length} items` : "details" });
    }
  }
  return out;
}

export type TrailFilter = { rule: TrailRule | ""; outcome: TrailOutcome | ""; ticker: string };
export const EMPTY_TRAIL_FILTER: TrailFilter = { rule: "", outcome: "", ticker: "" };
export const TICKER_PATTERN = /^[A-Za-z0-9._-]{1,32}$/;

/** The ticker filter only when it is valid for the API (otherwise the call would be a 422). */
export function validTicker(text: string): string | null {
  const t = text.trim();
  return TICKER_PATTERN.test(t) ? t : null;
}

/** Appends a page, newest first, skipping rows already shown (a refetch may overlap). */
export function appendPage(existing: readonly TrailRow[], page: readonly TrailRow[]): TrailRow[] {
  const seen = new Set(existing.map((r) => r.id));
  return [...existing, ...page.filter((r) => !seen.has(r.id))];
}

/** Counts per outcome kind, for the one-line summary above the table. */
export function summarize(rows: readonly TrailRow[]): Record<OutcomeKind, number> {
  const counts: Record<OutcomeKind, number> = { alerted: 0, suppressed: 0, informational: 0, provisional: 0 };
  for (const r of rows) counts[outcomeMeta(r.outcome).kind] += 1;
  return counts;
}

/** The trail panel's load state; an `error` keeps the rows and cursor loaded so far so paging can be retried. */
export type TrailLoad =
  | { kind: "loading" }
  | { kind: "error"; message: string; rows: TrailRow[]; next: number | null }
  | { kind: "ok"; rows: TrailRow[]; next: number | null; paging: boolean };

/**
 * The state after an "older decisions" request settles. A response from an earlier filter
 * (`requestEpoch` differs from `currentEpoch`, which advances on every filter change) is dropped
 * so it can never replace the new filter's rows; a failure keeps the rows and the cursor.
 */
export function settleOlderPage(
  current: TrailLoad,
  requestEpoch: number,
  currentEpoch: number,
  result: { page: { rows: readonly TrailRow[]; next: number | null } } | { error: string },
): TrailLoad {
  if (requestEpoch !== currentEpoch || current.kind === "loading") return current;
  if ("error" in result) return { kind: "error", message: result.error, rows: current.rows, next: current.next };
  return { kind: "ok", rows: appendPage(current.rows, result.page.rows), next: result.page.next, paging: false };
}

/** Whether older decisions can be requested (also after a failed page, to retry). */
export function canLoadOlder(load: TrailLoad): boolean {
  return (load.kind === "ok" || load.kind === "error") && load.next !== null && !(load.kind === "ok" && load.paging);
}

/** "End of the trail" is true only when a successful load reached the last page. */
export function atEndOfTrail(load: TrailLoad): boolean {
  return load.kind === "ok" && load.next === null;
}
