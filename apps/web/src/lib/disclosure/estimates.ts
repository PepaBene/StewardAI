import type { components } from "@/lib/api/schema";

/*
 * Published estimates (plan P7): types and pure logic for the page. Same rules as the backend's
 * `disclosure/estimates.py` (the backend is the reference; this copy only powers the labelled sample):
 * five series are separate lanes and are never merged; two snapshots are compared only when issuer,
 * series, metric, segment, fiscal period, unit, currency, basis and panel methodology all agree,
 * otherwise a typed break reason and no number; a surprise uses only a snapshot known strictly before
 * the release; a percentage exists only when the base is not zero; a number that is not stated is null,
 * never zero.
 */

type S = components["schemas"];
export type EstimatesView = S["EstimatesOut"];
export type EstimateLine = S["EstimateLineOut"];
export type RevisionPair = S["RevisionPairOut"];
export type EventComparison = S["EventComparisonOut"];
export type SegmentBridge = S["EstimateSegmentBridgeOut"];
export type SegmentLine = S["SegmentLineOut"];
export type CoverageGap = S["CoverageGapOut"];
export type ConsensusSource = S["ConsensusSourceOut"];
export type RevisionGate = S["RevisionGateOut"];

export const SERIES = ["published_analyst_consensus", "named_individual_estimate", "company_guidance", "event_calendar_estimate", "owner_scenario"] as const;
export type Series = (typeof SERIES)[number];

export const LANE_META: Record<Series, { label: string; mark: string; what: string }> = {
  published_analyst_consensus: { label: "Published consensus", mark: "range bar, mean tick, median notch", what: "A file an issuer or vendor published. Not every analyst." },
  named_individual_estimate: { label: "Named analyst estimates", mark: "one dot per analyst", what: "Verified, named analyst and broker only." },
  company_guidance: { label: "Company guidance", mark: "bracket", what: "A management target. Never an analyst view." },
  event_calendar_estimate: { label: "Event-calendar estimate", mark: "hollow square", what: "One vendor number for one event, not a panel." },
  owner_scenario: { label: "Your scenario", mark: "dashed outline", what: "Your own modelled number. Never mixed with published ones." },
};

export const BREAK_LABEL: Record<string, string> = {
  basis_changed: "the accounting basis or adjusted definition changed",
  currency_changed: "the currency changed",
  panel_method_changed: "the panel or its methodology changed",
  period_redefined: "the fiscal period was redefined",
  unit_changed: "the unit or scale changed",
  restated: "the company restated or recast the prior figures",
};

export const STATUS_LABEL: Record<string, string> = {
  scored: "Scored",
  no_prior_estimate: "No estimate known before the release",
  actual_not_loaded: "Actual not loaded",
  not_comparable: "Not comparable",
  estimate_missing: "No estimate published",
  actual_missing: "Actual not loaded",
  taxonomy_break: "Segment definitions differ (recast)",
};

export const ACCESS_LABEL: Record<string, string> = {
  untested: "Not tested yet",
  available: "Available",
  authentication_failed: "Sign-in failed",
  retrieval_failed: "File could not be retrieved",
  paid_only: "Behind a paywall",
  not_covered: "Not covered",
};

export const num = (v: string | null | undefined): number | null => {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

const SCALE_WORD: Record<number, string> = { 0: "", 3: " thousands", 6: " millions", 9: " billions" };

/** "USD millions", "USD per share", "percent": the unit line printed next to every figure. */
export function unitLabel(l: Pick<EstimateLine, "unit" | "currency" | "scale">): string {
  if (l.unit === "percent") return "percent";
  if (l.unit === "per_share") return `${l.currency ?? "currency not stated"} per share`;
  if (l.unit === "count") return "count";
  return `${l.currency ?? "currency not stated"}${SCALE_WORD[l.scale] ?? ` (×10^${l.scale})`}`;
}

/** A stated figure in the line's own unit; null prints as "not stated" (never 0). */
export function figure(v: string | null | undefined, unit: string): string {
  const n = num(v);
  if (n == null) return "not stated";
  const d = unit === "per_share" ? 2 : Math.abs(n) >= 100 ? 1 : 2;
  return unit === "percent" ? `${n.toFixed(1)}%` : n.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });
}

export const analystsLabel = (n: number | null | undefined): string => (n == null ? "analyst count not stated" : `${n} ${n === 1 ? "analyst" : "analysts"}`);

export const signed = (n: number | null, digits = 1): string => (n == null ? "—" : `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(digits)}`);

/** In base units (value × 10^scale) so lines of different scales are compared honestly. */
export const baseValue = (v: string | null | undefined, scale: number): number | null => {
  const n = num(v);
  return n == null ? null : n * 10 ** scale;
};

export function statisticOf(l: EstimateLine): { stat: "mean" | "median" | "midpoint"; value: number } | null {
  const mean = num(l.mean);
  if (mean != null) return { stat: "mean", value: mean };
  const median = num(l.median);
  if (median != null) return { stat: "median", value: median };
  const lo = num(l.low);
  const hi = num(l.high);
  return lo != null && hi != null ? { stat: "midpoint", value: (lo + hi) / 2 } : null;
}

// --------------------------------------------------------------------------------- the cutoff
const t = (iso: string) => Date.parse(iso);

/** Lines known by `asOfMs` (strictly before it when `strict`); a snapshot a known correction replaced is dropped. */
export function visibleAt(lines: readonly EstimateLine[], asOfMs: number, strict = false): EstimateLine[] {
  const known = lines.filter((l) => (strict ? t(l.known_at) < asOfMs : t(l.known_at) <= asOfMs));
  const replaced = new Set(known.map((l) => l.supersedes_snapshot_id).filter((x): x is number => x != null));
  return known.filter((l) => !replaced.has(l.snapshot_id));
}

export const groupKey = (l: EstimateLine): string =>
  [l.series, l.series === "named_individual_estimate" ? `${l.broker}|${l.analyst}` : "", l.metric.toLowerCase(), (l.segment ?? "").toLowerCase(), l.fiscal_period].join("¦");

export function breakReasons(a: EstimateLine, b: EstimateLine): string[] {
  const out: string[] = [];
  if (a.period_start !== b.period_start || a.period_end !== b.period_end || a.fiscal_calendar !== b.fiscal_calendar) out.push("period_redefined");
  if (a.segment_taxonomy != null && b.segment_taxonomy != null && a.segment_taxonomy !== b.segment_taxonomy) out.push("restated");
  if (a.unit !== b.unit || a.scale !== b.scale) out.push("unit_changed");
  if (a.currency !== b.currency) out.push("currency_changed");
  if (a.basis !== b.basis || (a.basis_definition ?? "").trim().toLowerCase() !== (b.basis_definition ?? "").trim().toLowerCase()) out.push("basis_changed");
  if (a.panel_method !== b.panel_method) out.push("panel_method_changed");
  return out;
}

/** The revision from `a` to `b`, or a break. A break carries no change, no percent and no direction. */
export function revisionOf(a: EstimateLine, b: EstimateLine): RevisionPair {
  const common = {
    earlier_id: a.id,
    later_id: b.id,
    series: b.series,
    metric: b.metric,
    segment: b.segment,
    fiscal_period: b.fiscal_period,
    earlier_known_at: a.known_at,
    later_known_at: b.known_at,
    elapsed_days: Math.max(0, Math.floor((t(b.known_at) - t(a.known_at)) / 86_400_000)),
    formula_version: "revision.v1",
  };
  const blank = { statistic: null, earlier_value: null, later_value: null, change: null, change_pct: null, pct_note: null, analyst_count_change: null, analyst_count_note: null, direction: null };
  const reasons = breakReasons(a, b);
  if (reasons.length) return { ...common, ...blank, comparable: false, break_reasons: reasons, note: `Not comparable: ${reasons.map((r) => BREAK_LABEL[r]).join("; ")}.` };
  const sa = statisticOf(a);
  const sb = statisticOf(b);
  if (!sa || !sb || sa.stat !== sb.stat) return { ...common, ...blank, comparable: true, break_reasons: [], note: "The two snapshots do not state the same statistic, so no revision is computed." };
  const change = sb.value - sa.value;
  const countKnown = a.analyst_count != null && b.analyst_count != null;
  return {
    ...common,
    comparable: true,
    break_reasons: [],
    statistic: sa.stat,
    earlier_value: String(sa.value),
    later_value: String(sb.value),
    change: String(change),
    change_pct: sa.value === 0 ? null : String((change / Math.abs(sa.value)) * 100),
    pct_note: sa.value === 0 ? "percent is undefined because the earlier estimate is zero" : null,
    analyst_count_change: countKnown ? b.analyst_count! - a.analyst_count! : null,
    analyst_count_note: countKnown ? null : "analyst count not stated in one or both snapshots",
    direction: change > 0 ? "up" : change < 0 ? "down" : "flat",
    note: null,
  };
}

/** Consecutive snapshots of each series, newest pair first. Nothing is interpolated between them. */
export function revisionPairs(lines: readonly EstimateLine[]): RevisionPair[] {
  const groups = new Map<string, EstimateLine[]>();
  for (const l of lines) if (l.problems.length === 0) groups.set(groupKey(l), [...(groups.get(groupKey(l)) ?? []), l]);
  const out: RevisionPair[] = [];
  for (const rows of groups.values()) {
    rows.sort((x, y) => t(x.known_at) - t(y.known_at) || x.id - y.id);
    for (let i = 1; i < rows.length; i++) {
      if (rows[i - 1].snapshot_id === rows[i].snapshot_id || rows[i - 1].known_at === rows[i].known_at) continue;
      out.push(revisionOf(rows[i - 1], rows[i]));
    }
  }
  return out.sort((x, y) => t(y.later_known_at) - t(x.later_known_at) || y.later_id - x.later_id);
}

// ------------------------------------------------------------------------------- the surprise
export type Actual = { observation_id: number | null; value: number; scale: number; unit: string; currency: string | null; basis: string | null; released_at: string };

export const SURPRISE_FORMULA =
  "surprise = reported actual − estimate; percent = surprise ÷ |estimate|, defined only when the estimate is not zero. The estimate is the mean (else the median, else the midpoint of low and high) of the latest snapshot known strictly before the release.";

/** Client copy of the backend surprise: latest consensus snapshot known strictly before the release. */
export function surpriseFor(actual: Actual | null, consensus: readonly EstimateLine[], metric: string, periodEnd: string): EventComparison {
  const base: EventComparison = {
    status: "actual_not_loaded", metric, segment: null, fiscal_period: null, period_end: periodEnd, release_at: actual?.released_at ?? null, estimate_value_id: null, estimate_known_at: null, statistic: null,
    estimate: null, actual: actual ? String(actual.value * 10 ** actual.scale) : null, surprise: null, surprise_pct: null, pct_note: null, analyst_count: null, basis: actual?.basis ?? null,
    currency: actual?.currency ?? null, unit: actual?.unit ?? null, later_files_only: false, warnings: [], actual_observation_id: actual?.observation_id ?? null, formula_version: "surprise.v1", formula: SURPRISE_FORMULA,
  };
  if (!actual) return { ...base, warnings: ["The reported actual is not loaded, so no surprise is computed."] };
  const pool = consensus.filter((l) => l.series === "published_analyst_consensus" && l.metric.toLowerCase() === metric.toLowerCase() && !l.segment && l.period_end === periodEnd && l.problems.length === 0);
  const release = t(actual.released_at);
  const before = visibleAt(pool, release, true);
  if (before.length === 0) {
    const later = pool.some((l) => t(l.known_at) >= release);
    return { ...base, status: "no_prior_estimate", later_files_only: later, fiscal_period: pool[0]?.fiscal_period ?? null, warnings: [later ? "Only a file published after the release exists; it cannot score this surprise." : "No snapshot known before the release exists for this metric and period."] };
  }
  const est = before.reduce((x, y) => (t(y.known_at) > t(x.known_at) || (y.known_at === x.known_at && y.id > x.id) ? y : x));
  const stat = statisticOf(est);
  const warnings: string[] = [];
  if (est.currency !== actual.currency) warnings.push("currency differs between estimate and actual");
  if (est.unit !== actual.unit) warnings.push("unit differs between estimate and actual");
  if (actual.basis != null && est.basis !== actual.basis) warnings.push(`basis differs: estimate is ${est.basis}, actual is ${actual.basis}`);
  if (actual.basis == null) warnings.push("the actual's basis is not stated, so basis equality is unverified");
  if (est.analyst_count == null) warnings.push("analyst count not stated");
  const head = { ...base, fiscal_period: est.fiscal_period, estimate_value_id: est.id, estimate_known_at: est.known_at, analyst_count: est.analyst_count, warnings };
  if (!stat || warnings.some((w) => /differs/.test(w))) return { ...head, status: "not_comparable" };
  const e = stat.value * 10 ** est.scale;
  const a = actual.value * 10 ** actual.scale;
  return { ...head, status: "scored", statistic: stat.stat, estimate: String(e), surprise: String(a - e), surprise_pct: e === 0 ? null : String(((a - e) / Math.abs(e)) * 100), pct_note: e === 0 ? "percent is undefined because the estimate is zero" : null, basis: est.basis, currency: est.currency, unit: est.unit };
}

// -------------------------------------------------------------------------------- the lanes
export function lanesOf(lines: readonly EstimateLine[]): Record<Series, EstimateLine[]> {
  const out = Object.fromEntries(SERIES.map((s) => [s, [] as EstimateLine[]])) as Record<Series, EstimateLine[]>;
  for (const l of lines) if ((SERIES as readonly string[]).includes(l.series)) out[l.series as Series].push(l);
  for (const s of SERIES) out[s].sort((x, y) => t(y.known_at) - t(x.known_at) || y.id - x.id);
  return out;
}

export const allLines = (v: EstimatesView): EstimateLine[] => SERIES.flatMap((s) => v.lanes[s]);

/** The latest line of one lane for one metric and period (no segment). */
export function latestFor(lane: readonly EstimateLine[], metric: string, period: string): EstimateLine | null {
  return lane.filter((l) => l.metric === metric && l.fiscal_period === period && !l.segment).sort((a, b) => t(b.known_at) - t(a.known_at) || b.id - a.id)[0] ?? null;
}

export type Choice = { key: string; metric: string; period: string; label: string };

/** Every metric and fiscal period that any lane holds (consolidated lines only), oldest period first. */
export function choices(lines: readonly EstimateLine[]): Choice[] {
  const seen = new Map<string, Choice & { end: string }>();
  for (const l of lines) {
    if (l.segment) continue;
    const key = `${l.metric}¦${l.fiscal_period}`;
    if (!seen.has(key)) seen.set(key, { key, metric: l.metric, period: l.fiscal_period, label: `${l.metric}, ${l.fiscal_period}`, end: l.period_end ?? "" });
  }
  return [...seen.values()].sort((a, b) => a.metric.localeCompare(b.metric) || a.end.localeCompare(b.end));
}

/** Lines of the other lanes that the shared axis cannot carry because their currency, unit or scale differs. */
export const axisUnitKey = (l: Pick<EstimateLine, "currency" | "unit" | "scale">) => `${l.currency ?? ""}|${l.unit}|${l.scale}`;

export function gateWords(g: RevisionGate): string {
  return g.met ? `Revision alerts and screens are on: ${g.reason}.` : `Revision alerts and screens are off: ${g.reason}.`;
}

/** Warnings for the basis/panel of the lines drawn, in plain words. */
export function lineWarnings(l: EstimateLine): string[] {
  const out: string[] = [];
  if (l.analyst_count == null && l.series === "published_analyst_consensus") out.push("analyst count not stated");
  if (l.basis === "adjusted") out.push(l.basis_definition ? `adjusted: ${l.basis_definition}` : "adjusted, definition not stated");
  if (l.panel_method == null && l.series === "published_analyst_consensus") out.push("panel methodology not stated");
  if (l.supersedes_snapshot_id != null) out.push("this corrected file replaces an earlier one; the earlier file still scores the earlier surprise");
  return out;
}
