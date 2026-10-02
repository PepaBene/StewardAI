import { isFiniteNumber } from "@/lib/charts/scale";

import type {
  CashWalk,
  DebtSchedule,
  EarningsEvent,
  EstimateSet,
  ForwardPoint,
  LineVersion,
  PeerRow,
  PeerSet,
  SegmentSet,
  StatementLine,
  StatementPeriod,
  VersionMode,
} from "./types";

// ---------------------------------------------------------------------------------------
// As-filed versions and point-in-time
// ---------------------------------------------------------------------------------------

/** The version a mode selects for a period, or null when none was public yet. */
export function versionAt(period: StatementPeriod, mode: VersionMode): LineVersion | null {
  const versions = [...period.versions].sort((a, b) => a.version - b.version);
  if (versions.length === 0) return null;
  if (mode.kind === "latest") return versions[versions.length - 1];
  if (mode.kind === "original") return versions[0];
  const known = versions.filter((v) => v.filedAt.slice(0, 10) <= mode.date.slice(0, 10));
  return known.length ? known[known.length - 1] : null;
}

export type LinePoint = { period: string; end: string; value: number | null; restated: boolean; version: number | null };

/**
 * The line's values under a mode. A period filed after an as-of date has value null (not yet
 * known), and `restated` marks a displayed value that is itself a restatement.
 */
export function linePoints(line: StatementLine, mode: VersionMode): LinePoint[] {
  return line.periods.map((p) => {
    const v = versionAt(p, mode);
    return { period: p.period, end: p.end, value: v ? v.value : null, restated: !!v && v.restates, version: v ? v.version : null };
  });
}

/** Periods whose latest version differs from the original: what a restatement changed. */
export function restatements(line: StatementLine): { period: string; original: number; latest: number; delta: number; filedAt: string }[] {
  const out: { period: string; original: number; latest: number; delta: number; filedAt: string }[] = [];
  for (const p of line.periods) {
    const first = versionAt(p, { kind: "original" });
    const last = versionAt(p, { kind: "latest" });
    if (first && last && first.version !== last.version && first.value !== last.value) {
      out.push({ period: p.period, original: first.value, latest: last.value, delta: last.value - first.value, filedAt: last.filedAt });
    }
  }
  return out;
}

/** Every filing date in the statements, newest first: the choices for an "as known on" selector. */
export function knownDates(lines: readonly StatementLine[]): string[] {
  const dates = new Set<string>();
  for (const l of lines) for (const p of l.periods) for (const v of p.versions) dates.add(v.filedAt.slice(0, 10));
  return [...dates].sort().reverse();
}

// ---------------------------------------------------------------------------------------
// Cash-to-earnings walk
// ---------------------------------------------------------------------------------------

export type WalkStep = {
  label: string;
  /** The change this step contributes (for totals, the running level). */
  value: number;
  kind: "start" | "delta" | "subtotal" | "end";
  /** Bar spans [from, to] on the value axis. */
  from: number;
  to: number;
};

export type WalkResult = {
  steps: WalkStep[];
  /** Net income plus adjustments, before comparison to the reported figure. */
  derivedCfo: number;
  /** derivedCfo − reportedCfo: nonzero means the parts do not explain the filed total. */
  cfoGap: number;
  derivedFcf: number;
  fcfGap: number;
  reconciles: boolean;
  /** Free cash flow ÷ net income; null when net income is not positive. */
  fcfConversion: number | null;
  cfoConversion: number | null;
};

/**
 * Net income → operating cash flow → free cash flow, with a running level for each step and
 * a reconciliation against the *filed* totals. A walk whose parts do not add up to the
 * reported figures (beyond `tolerance` of the larger total) is flagged and must not feed
 * valuation or peer use.
 */
export function cashWalk(w: CashWalk, tolerance = 0.005): WalkResult {
  const steps: WalkStep[] = [];
  let level = w.netIncome;
  steps.push({ label: "Net income", value: level, kind: "start", from: 0, to: level });
  for (const a of w.adjustments) {
    const from = level;
    level += a.value;
    steps.push({ label: a.label, value: a.value, kind: "delta", from, to: level });
  }
  const derivedCfo = level;
  steps.push({ label: "Operating cash flow", value: derivedCfo, kind: "subtotal", from: 0, to: derivedCfo });
  const from = level;
  level -= Math.abs(w.capex);
  steps.push({ label: "Capital expenditure", value: -Math.abs(w.capex), kind: "delta", from, to: level });
  const derivedFcf = level;
  steps.push({ label: "Free cash flow", value: derivedFcf, kind: "end", from: 0, to: derivedFcf });

  const cfoGap = derivedCfo - w.reportedCfo;
  const fcfGap = derivedFcf - w.reportedFcf;
  const scale = Math.max(Math.abs(w.reportedCfo), Math.abs(w.reportedFcf), 1);
  return {
    steps,
    derivedCfo,
    cfoGap,
    derivedFcf,
    fcfGap,
    reconciles: Math.abs(cfoGap) / scale <= tolerance && Math.abs(fcfGap) / scale <= tolerance,
    fcfConversion: w.netIncome > 0 ? w.reportedFcf / w.netIncome : null,
    cfoConversion: w.netIncome > 0 ? w.reportedCfo / w.netIncome : null,
  };
}

// ---------------------------------------------------------------------------------------
// Debt maturities
// ---------------------------------------------------------------------------------------

export type DebtBucket = {
  /** Calendar year, or "later" for beyond the horizon. */
  key: string;
  label: string;
  /** Amount per currency, in the reporting currency. */
  byCurrency: Record<string, number>;
  total: number;
};

export type DebtBreakdown = {
  buckets: DebtBucket[];
  currencies: string[];
  /** Maturity-classified total in reporting currency. */
  classified: number;
  /** reportedTotal − classified, never negative: the explicit unclassified remainder. */
  unclassified: number;
  unclassifiedShare: number | null;
  /** The classified amount exceeds the reported total: a data error, not a negative remainder. */
  overClassified: boolean;
  hasNonReportingCurrency: boolean;
};

export function debtBreakdown(d: DebtSchedule, horizonYears = 6): DebtBreakdown {
  const rate = (c: string) => (c === d.reportingCurrency ? 1 : d.fxToReporting[c]);
  const buckets: DebtBucket[] = [];
  for (let k = 0; k < horizonYears; k++) {
    const y = d.baseYear + k;
    buckets.push({ key: String(y), label: String(y), byCurrency: {}, total: 0 });
  }
  const later: DebtBucket = { key: "later", label: `After ${d.baseYear + horizonYears - 1}`, byCurrency: {}, total: 0 };
  const currencies = new Set<string>();
  let classified = 0;
  for (const t of d.tranches) {
    const r = rate(t.currency);
    if (!isFiniteNumber(r) || !isFiniteNumber(t.amount) || t.amount <= 0 || t.year < d.baseYear) continue;
    const amount = t.amount * r;
    const b = buckets.find((x) => x.key === String(t.year)) ?? later;
    b.byCurrency[t.currency] = (b.byCurrency[t.currency] ?? 0) + amount;
    b.total += amount;
    classified += amount;
    currencies.add(t.currency);
  }
  const all = later.total > 0 ? [...buckets, later] : buckets;
  const unclassified = Math.max(0, d.reportedTotal - classified);
  return {
    buckets: all,
    currencies: [...currencies].sort((a, b) => (a === d.reportingCurrency ? -1 : b === d.reportingCurrency ? 1 : a.localeCompare(b))),
    classified,
    unclassified,
    unclassifiedShare: d.reportedTotal > 0 ? unclassified / d.reportedTotal : null,
    overClassified: classified > d.reportedTotal * (1 + 1e-6),
    hasNonReportingCurrency: [...currencies].some((c) => c !== d.reportingCurrency),
  };
}

// ---------------------------------------------------------------------------------------
// Segments
// ---------------------------------------------------------------------------------------

export type SegmentColorBy = "growth" | "margin";

export type SegmentAlignment = {
  growth: { aligned: boolean; reason: string | null };
  margin: { aligned: boolean; reason: string | null };
};

/**
 * Whether segments may be coloured by growth or margin on one scale. Growth needs a prior-year
 * figure for every segment; margin additionally needs one shared profit definition. When not
 * aligned the treemap keeps the area (reported revenue) and drops the colour, with the reason.
 */
export function segmentAlignment(set: SegmentSet): SegmentAlignment {
  const segs = set.segments;
  const missingPrior = segs.filter((s) => !isFiniteNumber(s.priorRevenue) || s.priorRevenue <= 0);
  const growth = missingPrior.length
    ? { aligned: false, reason: `No prior-year revenue for ${missingPrior.map((s) => s.name).join(", ")}.` }
    : { aligned: true, reason: null };
  const noProfit = segs.filter((s) => !isFiniteNumber(s.operatingProfit));
  const defs = new Set(segs.map((s) => s.profitDefinition));
  const margin = noProfit.length
    ? { aligned: false, reason: `No segment profit for ${noProfit.map((s) => s.name).join(", ")}.` }
    : defs.size > 1
      ? { aligned: false, reason: `Segment profit is defined differently (${[...defs].filter(Boolean).join(" vs ")}); margins are not comparable.` }
      : { aligned: true, reason: null };
  return { growth, margin };
}

/** A treemap tile: area = reported revenue, colour value null when the colour is not comparable. */
export type SegmentTile = { id: string; label: string; weight: number; change: number | null };

export function segmentTiles(set: SegmentSet, colorBy: SegmentColorBy): { items: SegmentTile[]; colored: boolean; reason: string | null; cap: number } {
  const align = segmentAlignment(set)[colorBy];
  const items: SegmentTile[] = set.segments
    .filter((s) => s.revenue > 0)
    .map((s) => {
      let change: number | null = null;
      if (align.aligned) {
        change = colorBy === "growth" ? s.revenue / s.priorRevenue! - 1 : s.operatingProfit! / s.revenue;
      }
      return { id: s.name, label: s.name, weight: s.revenue, change };
    });
  return { items, colored: align.aligned, reason: align.reason, cap: colorBy === "growth" ? 0.2 : 0.4 };
}

// ---------------------------------------------------------------------------------------
// Forward estimates
// ---------------------------------------------------------------------------------------

/** The same estimate series across vintages: same metric, period, basis, currency and provider. */
export function comparableSnapshots(points: readonly ForwardPoint[], ref: Pick<ForwardPoint, "metric" | "period" | "basis" | "currency" | "provider" | "kind">): ForwardPoint[] {
  return points
    .filter((p) => p.metric === ref.metric && p.period === ref.period && p.basis === ref.basis && p.currency === ref.currency && p.provider === ref.provider && p.kind === ref.kind && isFiniteNumber(p.mean))
    .sort((a, b) => a.vintage.localeCompare(b.vintage));
}

export type RevisionSlope = {
  period: string;
  from: { vintage: string; mean: number };
  to: { vintage: string; mean: number };
  change: number;
  pct: number | null;
  days: number;
  perMonthPct: number | null;
};

const dayMs = 86_400_000;

/**
 * Revision of the consensus mean between the first and last comparable snapshot. Snapshots
 * that differ in basis, currency or provider are never joined: they yield no slope.
 */
export function revisionSlope(snapshots: readonly ForwardPoint[], ref: ForwardPoint): RevisionSlope | null {
  const s = comparableSnapshots(snapshots, ref);
  if (s.length < 2) return null;
  const a = s[0];
  const b = s[s.length - 1];
  const days = Math.max(1, Math.round((Date.parse(b.vintage) - Date.parse(a.vintage)) / dayMs));
  const change = b.mean! - a.mean!;
  const pct = a.mean! !== 0 ? change / Math.abs(a.mean!) : null;
  return {
    period: ref.period,
    from: { vintage: a.vintage, mean: a.mean! },
    to: { vintage: b.vintage, mean: b.mean! },
    change,
    pct,
    days,
    perMonthPct: pct !== null ? (pct / days) * 30 : null,
  };
}

export type Surprise = {
  /** actual / mean − 1; null with no consensus. */
  pct: number | null;
  /** Position vs the analyst range. */
  position: "above_range" | "within_range" | "below_range" | "no_range";
};

/** Actual versus available consensus. A missing consensus yields null: the model never creates one. */
export function surprise(e: Pick<EarningsEvent, "actual" | "consensusMean" | "consensusLow" | "consensusHigh">): Surprise {
  const pct = isFiniteNumber(e.consensusMean) && e.consensusMean !== 0 ? e.actual / e.consensusMean - 1 : null;
  if (!isFiniteNumber(e.consensusLow) || !isFiniteNumber(e.consensusHigh)) return { pct, position: "no_range" };
  return { pct, position: e.actual > e.consensusHigh ? "above_range" : e.actual < e.consensusLow ? "below_range" : "within_range" };
}

/** Periods with a forward point of the given kind, so a chart can say which kinds exist where. */
export function forwardByPeriod(set: EstimateSet): Map<string, ForwardPoint[]> {
  const m = new Map<string, ForwardPoint[]>();
  for (const p of set.forward) m.set(p.period, [...(m.get(p.period) ?? []), p]);
  return m;
}

// ---------------------------------------------------------------------------------------
// Peers
// ---------------------------------------------------------------------------------------

export type PeerExclusion = { name: string; ticker: string; reason: string };

export type PeerComparison = {
  metricId: string;
  label: string;
  definition: string;
  rows: { name: string; ticker: string; value: number; subject: boolean; basis: string }[];
  excluded: PeerExclusion[];
  covered: number;
  total: number;
  /** Median of covered peers (subject excluded); null with none. */
  peerMedian: number | null;
};

function median(values: readonly number[]): number | null {
  if (values.length === 0) return null;
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/**
 * One metric across the subject and its peers under the *one* current definition. A peer
 * whose figure uses another definition, has no value, or describes another fiscal period is
 * excluded with the reason (GAAP and IFRS peers compare only on a common definition), and the
 * covered/excluded counts are always reported.
 */
export function peerComparison(set: PeerSet, metricId: string): PeerComparison {
  const def = set.definitions[metricId];
  const reference = set.subject.metrics[metricId];
  const excluded: PeerExclusion[] = [];
  const rows: PeerComparison["rows"] = [];
  const take = (p: PeerRow, subject: boolean) => {
    const m = p.metrics[metricId];
    if (!m || !isFiniteNumber(m.value)) {
      excluded.push({ name: p.name, ticker: p.ticker, reason: "No value for this metric." });
    } else if (reference && m.definition !== reference.definition) {
      excluded.push({ name: p.name, ticker: p.ticker, reason: `Uses a different definition (${m.definition}); not comparable to ${reference.definition}.` });
    } else if (p.period !== set.subject.period) {
      excluded.push({ name: p.name, ticker: p.ticker, reason: `Describes ${p.period}, not ${set.subject.period}.` });
    } else {
      rows.push({ name: p.name, ticker: p.ticker, value: m.value, subject, basis: p.basis });
    }
  };
  take(set.subject, true);
  for (const p of set.peers) take(p, false);
  rows.sort((a, b) => b.value - a.value);
  const coveredPeers = rows.filter((r) => !r.subject);
  return {
    metricId,
    label: def?.label ?? metricId,
    definition: def?.definition ?? "No definition recorded",
    rows,
    excluded: excluded.filter((e) => e.ticker !== set.subject.ticker),
    covered: coveredPeers.length,
    total: set.peers.length,
    peerMedian: median(coveredPeers.map((r) => r.value)),
  };
}
