import type {
  FactOut,
  FactReview,
  FactStatus,
  JobSummary,
  KpiStatus,
  ReviewAction,
  VerdictStatus,
} from "@/lib/api/client";
import { isTicker } from "@/lib/portfolio/transactions";

import { formatFactValue } from "./confidence";

/*
 * The owner's review of a held, quarantined or verified fact: accept, correct or reject,
 * always with a reason (the API logs it with the decision). The worker then re-judges the
 * holding's verdicts; the screens poll the holding until that run has finished.
 */

export const REASON_MAX = 500;

export const REVIEW_ACTIONS = {
  accept: {
    label: "Accept",
    submit: "Accept figure",
    done: "Accepted",
    description: "The figure is right as extracted. It becomes verified and verdicts may use it.",
  },
  correct: {
    label: "Correct",
    submit: "Save correction",
    done: "Corrected",
    description: "The figure is wrong. Your value is stored as a new verified figure that wins; this one is kept, rejected.",
  },
  reject: {
    label: "Reject",
    submit: "Reject figure",
    done: "Rejected",
    description: "The figure is wrong or irrelevant. It is kept for the record and used nowhere.",
  },
} as const satisfies Record<ReviewAction, { label: string; submit: string; done: string; description: string }>;

/* Mirrors the API's rules (steward.earnings.review.ALLOWED_FROM); anything else is a 409. */
const ALLOWED_FROM: Record<ReviewAction, readonly FactStatus[]> = {
  accept: ["unverified", "quarantined"],
  correct: ["unverified", "quarantined", "verified"],
  reject: ["unverified", "quarantined", "verified"],
};

/** The actions the API accepts for a fact in this status, in display order. */
export function allowedReviewActions(status: FactStatus): ReviewAction[] {
  return (["accept", "correct", "reject"] as const).filter((a) => ALLOWED_FROM[a].includes(status));
}

/** Held (validator) or quarantined (reconciliation): waiting for the owner. */
export function isHeld(status: FactStatus): boolean {
  return status === "unverified" || status === "quarantined";
}

const PLAIN = /^-?(\d+(\.\d+)?|\.\d+)$/;
const GROUPED = /^-?\d{1,3}(,\d{3})+(\.\d+)?$/;

/**
 * A corrected value as typed ("94,036", "94036.5", "-1.2") -> an exact decimal string for
 * the API ("94036", "94036.5"), or null. Commas are only accepted as thousands separators.
 */
export function parseCorrectedValue(text: string): string | null {
  const t = text.trim().replace(/\s+/g, "");
  if (PLAIN.test(t)) return normalizeDecimal(t);
  if (GROUPED.test(t)) return normalizeDecimal(t.replaceAll(",", ""));
  return null;
}

function normalizeDecimal(t: string): string {
  const negative = t.startsWith("-");
  let [int, frac = ""] = (negative ? t.slice(1) : t).split(".");
  int = int.replace(/^0+(?=\d)/, "") || "0";
  frac = frac.replace(/0+$/, "");
  const out = frac ? `${int}.${frac}` : int;
  return negative && out !== "0" ? `-${out}` : out;
}

export type ReviewDraft = { action: ReviewAction | null; reason: string; value: string };
export type ReviewErrors = { action?: string; reason?: string; value?: string };

/** The request body, or the message to show next to each field. */
export function validateReview(
  draft: ReviewDraft,
): { ok: true; body: FactReview } | { ok: false; errors: ReviewErrors } {
  const errors: ReviewErrors = {};
  const reason = draft.reason.trim();
  if (!draft.action) errors.action = "Choose Accept, Correct or Reject.";
  if (!reason) errors.reason = "Give a reason; it is logged with the decision.";
  else if (reason.length > REASON_MAX) errors.reason = `Keep the reason to ${REASON_MAX} characters (${reason.length} now).`;
  let value: string | null = null;
  if (draft.action === "correct") {
    value = parseCorrectedValue(draft.value);
    if (!draft.value.trim()) errors.value = "Enter the right value.";
    else if (value === null) errors.value = "Enter a number, e.g. 94036 or 1.57.";
  }
  if (!draft.action || Object.keys(errors).length > 0) return { ok: false, errors };
  return {
    ok: true,
    body: draft.action === "correct" ? { action: "correct", reason, value: value! } : { action: draft.action, reason },
  };
}

const SCALE_WORD: Record<FactOut["scale"], string> = {
  units: "",
  thousands: "thousands",
  millions: "millions",
  billions: "billions",
};

const SCALE_FACTOR: Record<FactOut["scale"], number> = {
  units: 1,
  thousands: 1e3,
  millions: 1e6,
  billions: 1e9,
};

/**
 * How to type a correction: in the scale the figure was printed in. E.g. "Type the value in
 * millions of USD, as printed: 94036 means USD 94,036 m."
 */
export function scaleHint(fact: Pick<FactOut, "scale" | "unit" | "currency" | "value_as_written">): string {
  const example = `${fact.value_as_written} means ${formatFactValue(fact)}`;
  const scale = SCALE_WORD[fact.scale];
  switch (fact.unit) {
    case "percent":
      return `Type the percentage without the % sign, as printed: ${example}.`;
    case "per_share":
      return `Type the amount per share${fact.currency ? ` in ${fact.currency}` : ""}, as printed: ${example}.`;
    case "ratio":
      return `Type the ratio, as printed: ${example}.`;
    case "count":
      return scale ? `Type the count in ${scale}, as printed: ${example}.` : `Type the count, as printed: ${example}.`;
    default: {
      const what = [scale ? `${scale} of` : "", fact.currency ?? "the reporting currency"].filter(Boolean).join(" ");
      return `Type the value in ${what}, as printed${scale ? " (not in units)" : ""}: ${example}.`;
    }
  }
}

/** A value in units (as XBRL reports it) in the fact's printed scale: 94036000000, millions -> 94036. */
export function unitsToPrinted(units: number, scale: FactOut["scale"]): number {
  return units / SCALE_FACTOR[scale];
}

/** An XBRL value or difference (in units) written like the fact: "USD 94,036 m". */
export function formatInFactScale(
  units: number | null | undefined,
  fact: Pick<FactOut, "scale" | "unit" | "currency">,
  { signed = false, locale }: { signed?: boolean; locale?: string } = {},
): string {
  if (units === null || units === undefined || !Number.isFinite(units)) return "—";
  const written = new Intl.NumberFormat(locale, {
    maximumFractionDigits: 3,
    signDisplay: signed ? "exceptZero" : "auto",
  }).format(unitsToPrinted(units, fact.scale));
  return formatFactValue({ ...fact, value_as_written: written });
}

/** The review queue's filter, kept in the URL (?ticker=). */
export function parseReviewTicker(params: URLSearchParams): string | null {
  const ticker = params.get("ticker");
  return ticker && isTicker(ticker) ? ticker : null;
}

export function reviewQueueHref(ticker?: string | null): string {
  return ticker ? `/review?ticker=${encodeURIComponent(ticker)}` : "/review";
}

export type VerdictRunOutcome = "pending" | "succeeded" | "failed";

/**
 * Whether the verdict run queued by a review has finished, from the holding's latest
 * verdict run. A newer run that has finished also covers the review (it re-judged after it).
 */
export function verdictRunOutcome(lastRun: JobSummary | null | undefined, jobId: number): VerdictRunOutcome {
  if (!lastRun || lastRun.id < jobId) return "pending";
  if (lastRun.status === "succeeded" || lastRun.status === "failed") return lastRun.status;
  return "pending";
}

/** KPI metric and fact metric are the same figure or one derives from the other ("revenue" -> "revenue_growth_yoy"). */
export function metricsRelated(kpiMetric: string, factMetric: string): boolean {
  return kpiMetric === factMetric || kpiMetric.startsWith(`${factMetric}_`) || factMetric.startsWith(`${kpiMetric}_`);
}

export type AffectedKpi = {
  kpi: KpiStatus["kpi"];
  current: KpiStatus["current"];
  /** Status before the review, when it was captured; null: never judged. */
  before: VerdictStatus | null | undefined;
  changed: boolean;
};

/**
 * The KPIs a review can move: those whose current verdict uses the fact (or its correction),
 * those whose metric relates to the fact's, and any whose status changed. When none match,
 * every KPI of the holding, so the recomputed state is always shown.
 */
export function affectedKpis(
  kpis: KpiStatus[],
  fact: { metric: string; ids: number[] },
  before?: Map<string, VerdictStatus | null>,
): AffectedKpi[] {
  const all = kpis.map(({ kpi, current }) => {
    const prior = before?.get(kpi.id);
    const now = current?.status ?? null;
    return { kpi, current, before: prior, changed: before !== undefined && before.has(kpi.id) && prior !== now };
  });
  const related = all.filter(
    (k) =>
      k.changed ||
      metricsRelated(k.kpi.metric, fact.metric) ||
      (k.current?.fact_ids ?? []).some((id) => fact.ids.includes(id)),
  );
  return related.length > 0 ? related : all;
}

/** KPI id -> current status (null: never judged), to compare after the run. */
export function kpiStatusSnapshot(kpis: KpiStatus[]): Map<string, VerdictStatus | null> {
  return new Map(kpis.map(({ kpi, current }) => [kpi.id, current?.status ?? null]));
}

/** The API's error message: FastAPI's `detail` string, or its 422 list. */
export function apiErrorDetail(error: unknown): string {
  if (!error || typeof error !== "object" || !("detail" in error)) return "";
  const detail = (error as { detail: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : ""))
      .filter(Boolean)
      .join("; ");
  }
  return "";
}

/** A failed review, in words: 409 (already decided), 404, 422 or anything else. */
export function describeReviewFailure(status: number, error: unknown): string {
  const detail = apiErrorDetail(error);
  if (status === 409) return `Not allowed for this figure now${detail ? `: ${detail}` : ""}. Reload to see its current status.`;
  if (status === 404) return `This figure no longer exists${detail ? ` (${detail})` : ""}.`;
  if (status === 422) return `The API rejected the input${detail ? `: ${detail}` : ""}.`;
  if (status === 0) return detail || "The API could not be reached.";
  return `The API answered HTTP ${status}${detail ? `: ${detail}` : ""}.`;
}

// ---------------------------------------------------------------------------
// Master / detail queue: order, tier filter, selection, evidence highlight, history
// ---------------------------------------------------------------------------

type QueueItem = { ticker: string; fact: Pick<FactOut, "id" | "extracted_at" | "source_tier"> };

/** Oldest extraction first (ties by figure id), so nothing waits longest unseen. */
export function sortOldestFirst<T extends QueueItem>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => a.fact.extracted_at.localeCompare(b.fact.extracted_at) || a.fact.id - b.fact.id);
}

/** The queue's source-tier filter: every tier, one tier, or figures from a host with no tier. */
export type TierFilter = "all" | "none" | 1 | 2 | 3;

export function parseTierFilter(value: string | null | undefined): TierFilter {
  if (value === "none") return "none";
  const n = Number(value);
  return n === 1 || n === 2 || n === 3 ? n : "all";
}

export function tierFilterLabel(filter: TierFilter): string {
  if (filter === "all") return "All tiers";
  if (filter === "none") return "No tier";
  return `Tier ${filter}`;
}

/** The filters worth offering for these items ("all" first), with how many each keeps. */
export function tierFilterOptions(items: readonly QueueItem[]): { filter: TierFilter; count: number }[] {
  const out: { filter: TierFilter; count: number }[] = [{ filter: "all", count: items.length }];
  for (const f of [1, 2, 3, "none"] as const) {
    const count = items.filter((i) => matchesTier(i, f)).length;
    if (count > 0) out.push({ filter: f, count });
  }
  return out;
}

export function matchesTier(item: QueueItem, filter: TierFilter): boolean {
  if (filter === "all") return true;
  if (filter === "none") return item.fact.source_tier === null;
  return item.fact.source_tier === filter;
}

/**
 * The id to select after moving `delta` places (J/K): stays inside the list, starts at the
 * first item when nothing (or an id no longer listed) is selected. Null for an empty list.
 */
export function moveSelection(ids: readonly number[], current: number | null, delta: number): number | null {
  if (ids.length === 0) return null;
  const at = current === null ? -1 : ids.indexOf(current);
  if (at < 0) return ids[0];
  return ids[Math.max(0, Math.min(ids.length - 1, at + delta))];
}

/**
 * The snippet split around the printed value, to highlight it: the exact value as written
 * first (any case), else its first number ("€18.1 billion" -> "18.1"). Null when the snippet
 * does not contain it.
 */
export function highlightValue(
  snippet: string,
  valueAsWritten: string,
): { before: string; match: string; after: string } | null {
  const tries = [valueAsWritten.trim()];
  const number = /-?\d[\d,]*(?:\.\d+)?/.exec(valueAsWritten)?.[0];
  if (number && number !== tries[0]) tries.push(number);
  const lower = snippet.toLowerCase();
  for (const t of tries) {
    if (!t) continue;
    const at = lower.indexOf(t.toLowerCase());
    if (at >= 0) return { before: snippet.slice(0, at), match: snippet.slice(at, at + t.length), after: snippet.slice(at + t.length) };
  }
  return null;
}

type HistoryFact = Pick<
  FactOut,
  "id" | "metric" | "basis" | "status" | "value_units" | "fiscal_year" | "fiscal_period" | "period_end" | "period"
>;

/** "Q2 FY2026" style short period label for a bar: "Q2·26", "FY·25", "H1·26". */
export function shortPeriod(fact: Pick<FactOut, "fiscal_year" | "fiscal_period">): string {
  const fp = fact.fiscal_period.replace(/^FY$/i, "FY");
  return `${fp}·${String(fact.fiscal_year).slice(-2)}`;
}

function periodKey(f: Pick<FactOut, "period_end" | "fiscal_year" | "fiscal_period">): string {
  return f.period_end ?? `${f.fiscal_year}-${f.fiscal_period}`;
}

/**
 * The same metric's other stored figures (same basis, not rejected, one per period: the
 * verified one when there are several), oldest first and at most `limit`, each in the
 * claim's printed scale, then the claim itself (`claim: true`). Empty when there is no
 * earlier figure to compare with.
 */
export function historyAgainstClaim(
  claim: HistoryFact & Pick<FactOut, "scale">,
  facts: readonly HistoryFact[],
  limit = 7,
): { id: number; label: string; value: number; claim: boolean }[] {
  const byPeriod = new Map<string, HistoryFact>();
  for (const f of facts) {
    if (f.id === claim.id || f.metric !== claim.metric || f.basis !== claim.basis || f.status === "rejected") continue;
    const key = periodKey(f);
    const seen = byPeriod.get(key);
    if (!seen || (seen.status !== "verified" && f.status === "verified")) byPeriod.set(key, f);
  }
  const past = [...byPeriod.values()]
    .sort((a, b) => periodKey(a).localeCompare(periodKey(b)))
    .slice(-limit);
  if (past.length === 0) return [];
  const bar = (f: HistoryFact, isClaim: boolean) => ({
    id: f.id,
    label: isClaim ? "Claim" : shortPeriod(f),
    value: unitsToPrinted(f.value_units, claim.scale),
    claim: isClaim,
  });
  return [...past.map((f) => bar(f, false)), bar(claim, true)];
}

/**
 * The filing's value to offer as a one-click correction: the latest reconciliation row with
 * an XBRL value (a mismatch first), in the fact's printed scale as an exact decimal string,
 * with its display text. Null when no filing value is stored.
 */
export function filedCorrection(
  fact: Pick<FactOut, "scale" | "unit" | "currency" | "value">,
  rows: readonly { xbrl_value: number | null; outcome: string; checked_at: string }[],
): { value: string; display: string } | null {
  const usable = rows
    .filter((r) => r.xbrl_value !== null && Number.isFinite(r.xbrl_value))
    .sort((a, b) => Number(b.outcome === "mismatch") - Number(a.outcome === "mismatch") || b.checked_at.localeCompare(a.checked_at));
  if (usable.length === 0) return null;
  const printed = unitsToPrinted(usable[0].xbrl_value!, fact.scale);
  const value = parseCorrectedValue(String(Number(printed.toPrecision(12))));
  if (value === null || Number(value) === fact.value) return null;
  return { value, display: formatInFactScale(usable[0].xbrl_value, fact) };
}

/** The thesis KPIs a figure feeds: the KPI's metric or one derived from it, same basis. */
export function feedsKpis<K extends { metric: string; basis: string }>(
  fact: Pick<FactOut, "metric" | "basis">,
  kpis: readonly K[],
): K[] {
  return kpis.filter((k) => k.basis === fact.basis && metricsRelated(k.metric, fact.metric));
}
