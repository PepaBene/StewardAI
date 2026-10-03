import { metricDef, VALUATION_METRICS } from "./metrics";
import type {
  Criterion,
  CriterionResult,
  Entity,
  HeldInput,
  LedgerGap,
  MetricDef,
  MissingTreatment,
  Outcome,
  Reading,
  ScopeFilter,
  ScreenInputs,
  ScreenSummary,
  UnknownReason,
} from "./types";

export const UNKNOWN_REASONS: readonly UnknownReason[] = ["missing", "stale", "not_covered", "paid_only", "unavailable"];

export const REASON_LABEL: Record<UnknownReason, string> = {
  missing: "Missing metric",
  stale: "Stale",
  not_covered: "Not covered",
  paid_only: "Paid only",
  unavailable: "Unavailable",
};

export const REASON_HELP: Record<UnknownReason, string> = {
  missing: "The source answered but has no value for this metric (the API's own reason is shown).",
  stale: "A value exists but the API flags its price or fundamentals as stale, so it is not used.",
  not_covered: "No source covers this entity or metric (or the metric needs a position the entity does not have).",
  paid_only: "The provider's free tier does not return it; it is never replaced by a paid call.",
  unavailable: "The request failed, so nothing is known about this entity.",
};

const upper = (t: string) => t.trim().toUpperCase();

/** The app's own universe: held positions plus watchlist items, one entity per ticker. */
export function buildUniverse(held: readonly HeldInput[], watchlist: readonly { ticker: string }[]): Entity[] {
  const map = new Map<string, Entity>();
  for (const h of held) map.set(upper(h.ticker), { ticker: h.ticker, name: h.name, held: true, watchlist: false });
  for (const w of watchlist) {
    const key = upper(w.ticker);
    const e = map.get(key);
    if (e) e.watchlist = true;
    else map.set(key, { ticker: w.ticker, name: null, held: false, watchlist: true });
  }
  return [...map.values()].sort((a, b) => a.ticker.localeCompare(b.ticker));
}

export function inScope(e: Entity, scope: ScopeFilter): boolean {
  return scope === "all" || (scope === "held" ? e.held : e.watchlist);
}

const PAID = /paid|premium|upgrade|subscription|plan does not/i;

function ledgerReason(gaps: readonly LedgerGap[] | undefined): { reason: "paid_only" | "not_covered"; detail: string } | null {
  if (!gaps || gaps.length === 0) return null;
  const paid = gaps.find((g) => g.rawState === "paid_only");
  if (paid) return { reason: "paid_only", detail: `Coverage ledger: ${paid.provider}/${paid.dataset} is paid_only for this entity${paid.reason ? ` (${paid.reason})` : ""}.` };
  const nc = gaps.find((g) => g.rawState === "not_covered");
  if (nc) return { reason: "not_covered", detail: `Coverage ledger: ${nc.provider}/${nc.dataset} does not cover this entity${nc.reason ? ` (${nc.reason})` : ""}.` };
  return null;
}

/**
 * One metric of one entity, as an API-returned value or an explicit unknown with its reason.
 * A missing figure is never converted to 0 and never passes a criterion.
 */
export function readMetric(entity: Entity, def: MetricDef, inputs: ScreenInputs): Reading {
  const key = upper(entity.ticker);
  if (def.source === "valuation") {
    const v = inputs.valuations.get(key);
    if (!v || v.kind === "loading") return { status: "unknown", reason: "unavailable", detail: "Still loading." };
    if (v.kind === "error") return { status: "unknown", reason: "unavailable", detail: `Valuation request failed: ${v.message}` };
    const gap = ledgerReason(inputs.ledger.get(key));
    if (v.kind === "not_found") return { status: "unknown", reason: gap?.reason ?? "not_covered", detail: gap?.detail ?? "The valuation endpoint has no record of this ticker." };
    const fig = v.ratios[def.id];
    if (fig && fig.value !== null && Number.isFinite(fig.value)) {
      const stale = (def.staleWhen.includes("price") && v.priceStale === true) || (def.staleWhen.includes("eps") && v.epsStale === true);
      if (stale) {
        const which = [def.staleWhen.includes("price") && v.priceStale === true ? "price" : null, def.staleWhen.includes("eps") && v.epsStale === true ? "fundamentals" : null].filter(Boolean).join(" and ");
        return { status: "unknown", reason: "stale", detail: `The API flags the ${which} behind this figure as stale.`, staleValue: fig.value };
      }
      return { status: "value", value: fig.value };
    }
    const apiReason = fig?.reason ?? null;
    if (!v.hasAnyData) return { status: "unknown", reason: gap?.reason ?? "not_covered", detail: gap?.detail ?? "The valuation endpoint returned neither a price nor fundamentals for this entity." };
    const errorText = v.errors.find((e) => PAID.test(e));
    if (errorText) return { status: "unknown", reason: "paid_only", detail: `Source error reported by the API: ${errorText}` };
    if (gap?.reason === "paid_only") return { status: "unknown", reason: "paid_only", detail: gap.detail };
    return { status: "unknown", reason: "missing", detail: apiReason ?? "The API returned no value and gave no reason." };
  }

  // Held-position metrics: only a held entity has them.
  if (!entity.held) return { status: "unknown", reason: "not_covered", detail: "Not a held position; this metric exists only for held positions." };
  if (def.source === "holdings") {
    const h = inputs.held.find((x) => upper(x.ticker) === key);
    const value = h ? (def.id === "weight" ? h.weight : h.pplPct) : null;
    return value !== null && value !== undefined && Number.isFinite(value) ? { status: "value", value } : { status: "unknown", reason: "missing", detail: "The portfolio snapshot has no value for this position." };
  }
  const p = inputs.prices.find((x) => upper(x.ticker) === key);
  if (!p) return { status: "unknown", reason: "unavailable", detail: "The price response does not list this position." };
  return p.dayChangePct !== null && Number.isFinite(p.dayChangePct)
    ? { status: "value", value: p.dayChangePct }
    : { status: "unknown", reason: "missing", detail: "No stored close pair, so no day change." };
}

/** Threshold text -> the API's unit (a % input becomes a fraction); null while the text is not a finite number. */
export function thresholdValue(c: Criterion): number | null {
  const def = metricDef(c.metric);
  const text = c.value.trim();
  if (!def || text === "") return null;
  const n = Number(text);
  if (!Number.isFinite(n)) return null;
  return def.unit === "pct" ? n / 100 : n;
}

export function isComplete(c: Criterion): boolean {
  return thresholdValue(c) !== null;
}

function judge(reading: Reading, c: Criterion): CriterionResult["verdict"] {
  if (reading.status !== "value") return "unknown";
  const t = thresholdValue(c);
  if (t === null) return "unknown";
  return (c.op === "gte" ? reading.value >= t : reading.value <= t) ? "pass" : "fail";
}

/**
 * One entity against the complete criteria. A definite failure wins over an unknown (it fails no
 * matter what the unknown is). Otherwise any unknown makes the entity excluded or unknown by the
 * chosen treatment: it is never matched.
 */
export function evaluateEntity(entity: Entity, criteria: readonly Criterion[], inputs: ScreenInputs, treatment: MissingTreatment): Outcome {
  const complete = criteria.filter(isComplete);
  const results: CriterionResult[] = complete.map((criterion) => {
    const def = metricDef(criterion.metric)!;
    const reading = readMetric(entity, def, inputs);
    return { criterion, reading, verdict: judge(reading, criterion) };
  });
  const key = upper(entity.ticker);
  const valuation = inputs.valuations.get(key);
  const pending = complete.some((c) => metricDef(c.metric)?.source === "valuation") && (valuation === undefined || valuation.kind === "loading");
  const reasons = [...new Set(results.filter((r) => r.reading.status === "unknown").map((r) => (r.reading as { reason: UnknownReason }).reason))];
  if (results.some((r) => r.verdict === "fail")) return { entity, kind: "failed", results, reasons: [] };
  if (pending) return { entity, kind: "pending", results, reasons: [] };
  if (results.some((r) => r.verdict === "unknown")) return { entity, kind: treatment === "exclude" ? "excluded" : "unknown", results, reasons };
  return { entity, kind: "matched", results, reasons: [] };
}

export function runScreen(entities: readonly Entity[], criteria: readonly Criterion[], inputs: ScreenInputs, treatment: MissingTreatment): Outcome[] {
  return entities.map((e) => evaluateEntity(e, criteria, inputs, treatment));
}

export function summarize(outcomes: readonly Outcome[]): ScreenSummary {
  const byReason = Object.fromEntries(UNKNOWN_REASONS.map((r) => [r, 0])) as Record<UnknownReason, number>;
  const s: ScreenSummary = { universe: outcomes.length, matched: 0, failed: 0, excluded: 0, unknown: 0, pending: 0, byReason };
  for (const o of outcomes) {
    s[o.kind] += 1;
    if (o.kind === "excluded" || o.kind === "unknown") for (const r of o.reasons) byReason[r] += 1;
  }
  return s;
}

/** Valuation-sourced metric ids (for the loader to know whether valuations are needed at all). */
export function needsValuations(criteria: readonly Criterion[]): boolean {
  return criteria.some((c) => VALUATION_METRICS.includes(c.metric as never));
}

/** Reading -> display text in the metric's unit; unknowns say what they are. */
export function formatReading(reading: Reading, def: MetricDef): string {
  const fmt = (v: number) => (def.unit === "pct" ? `${(v * 100).toFixed(1)}%` : `${v.toFixed(1)}x`);
  if (reading.status === "value") return fmt(reading.value);
  return reading.staleValue !== undefined ? `${fmt(reading.staleValue)} (stale)` : REASON_LABEL[reading.reason];
}
