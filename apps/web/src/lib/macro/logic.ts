import { extent, isFiniteNumber } from "@/lib/charts/scale";

import type {
  CountryComparisonRow,
  ExposureKind,
  HoldingExposureMap,
  MacroObservation,
  MacroSeries,
  TradeSlice,
  Vintage,
} from "./types";

const EPS = 1e-9;

// ---------------------------------------------------------------------------------------
// Vintages
// ---------------------------------------------------------------------------------------

/**
 * The value of an observation under a vintage. First release returns null when it was not
 * archived: that is `vintage_unavailable`, never the latest value under another name.
 */
export function valueFor(obs: MacroObservation, vintage: Vintage): number | null {
  if (vintage === "latest") return isFiniteNumber(obs.latest) ? obs.latest : null;
  return isFiniteNumber(obs.firstRelease) ? obs.firstRelease : null;
}

export function seriesPoints(series: MacroSeries, vintage: Vintage): { date: string; value: number | null }[] {
  return series.observations.map((o) => ({ date: o.period, value: valueFor(o, vintage) }));
}

/** Points whose latest value differs from the first release (the revision markers). */
export function revisedObservations(series: MacroSeries, tolerance = 1e-6): MacroObservation[] {
  return series.observations.filter(
    (o) => isFiniteNumber(o.firstRelease) && o.revisedAt !== null && Math.abs(o.latest - o.firstRelease) > tolerance,
  );
}

export type VintageSummary = {
  total: number;
  revised: number;
  /** Observations with no archived first release. */
  unavailable: number;
  /** Largest absolute revision, in the series' unit. */
  maxRevision: number | null;
};

export function vintageSummary(series: MacroSeries): VintageSummary {
  let maxRevision: number | null = null;
  for (const o of series.observations) {
    if (!isFiniteNumber(o.firstRelease)) continue;
    const d = Math.abs(o.latest - o.firstRelease);
    if (maxRevision === null || d > maxRevision) maxRevision = d;
  }
  return {
    total: series.observations.length,
    revised: revisedObservations(series).length,
    unavailable: series.observations.filter((o) => !isFiniteNumber(o.firstRelease)).length,
    maxRevision: maxRevision !== null && maxRevision > 1e-6 ? maxRevision : null,
  };
}

// ---------------------------------------------------------------------------------------
// Alignment and context statistics
// ---------------------------------------------------------------------------------------

/** The [earliest, latest] period across a set of series, so small multiples share one time axis. */
export function commonTimeDomain(list: readonly MacroSeries[]): [string, string] | null {
  const dates = list.flatMap((s) => s.observations.map((o) => o.period));
  if (dates.length === 0) return null;
  const sorted = [...dates].sort();
  return [sorted[0], sorted[sorted.length - 1]];
}

/** Days since 1970-01-01 for an ISO calendar day (time-zone independent). */
export function dayNumber(iso: string): number {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return Math.floor(Date.UTC(y, (m || 1) - 1, d || 1) / 86_400_000);
}

export type RangeStat = { low: number; high: number; years: number; count: number };

/**
 * The low/high of the latest-vintage values over the trailing `years` before the last
 * observation (that observation included), with how many points it rests on. Null with no data.
 */
export function trailingRange(series: MacroSeries, years: number): RangeStat | null {
  const obs = series.observations.filter((o) => isFiniteNumber(o.latest));
  if (obs.length === 0) return null;
  const last = obs[obs.length - 1].period;
  const cutoff = dayNumber(last) - Math.round(years * 365.25);
  const inWindow = obs.filter((o) => dayNumber(o.period) > cutoff);
  const ext = extent(inWindow.map((o) => o.latest));
  return ext ? { low: ext[0], high: ext[1], years, count: inWindow.length } : null;
}

/** Position of `value` within a range as 0..1, or null for a degenerate range. */
export function rangePosition(value: number, range: Pick<RangeStat, "low" | "high">): number | null {
  const span = range.high - range.low;
  if (!(span > 0)) return null;
  return Math.min(1, Math.max(0, (value - range.low) / span));
}

export function latestObservation(series: MacroSeries): MacroObservation | null {
  const obs = series.observations.filter((o) => isFiniteNumber(o.latest));
  return obs.length ? obs[obs.length - 1] : null;
}

/**
 * Alert gate (D2.8): only an observation for a period the app had not seen counts. A revision
 * of an already-known period never raises an alert.
 */
export function newObservations(
  previous: readonly Pick<MacroObservation, "period">[],
  current: readonly MacroObservation[],
): MacroObservation[] {
  const seen = new Set(previous.map((o) => o.period));
  return current.filter((o) => !seen.has(o.period));
}

// ---------------------------------------------------------------------------------------
// Ranked slopegraph
// ---------------------------------------------------------------------------------------

export type SlopeRow = CountryComparisonRow & {
  /** 1 = highest. Null when the value is missing at that end. */
  fromRank: number | null;
  toRank: number | null;
  /** Positive = moved up the ranking. Null unless ranked at both ends. */
  rankChange: number | null;
  change: number | null;
};

function rankDescending(values: readonly (number | null)[]): (number | null)[] {
  const order = values
    .map((v, i) => ({ v, i }))
    .filter((x): x is { v: number; i: number } => isFiniteNumber(x.v))
    .sort((a, b) => b.v - a.v || a.i - b.i);
  const ranks: (number | null)[] = values.map(() => null);
  // Ties share the better rank (competition ranking: 1, 2, 2, 4).
  order.forEach((x, k) => {
    ranks[x.i] = k > 0 && Math.abs(order[k - 1].v - x.v) < EPS ? ranks[order[k - 1].i] : k + 1;
  });
  return ranks;
}

/** Ranks each country at both ends; a country missing a value is unranked there, not zero. */
export function slopeRows(rows: readonly CountryComparisonRow[]): SlopeRow[] {
  const fromRanks = rankDescending(rows.map((r) => r.from));
  const toRanks = rankDescending(rows.map((r) => r.to));
  return rows.map((r, i) => ({
    ...r,
    fromRank: fromRanks[i],
    toRank: toRanks[i],
    rankChange: fromRanks[i] !== null && toRanks[i] !== null ? fromRanks[i]! - toRanks[i]! : null,
    change: isFiniteNumber(r.from) && isFiniteNumber(r.to) ? r.to - r.from : null,
  }));
}

// ---------------------------------------------------------------------------------------
// Trade
// ---------------------------------------------------------------------------------------

export type PartnerShare = { partner: string; valueUsd: number; share: number };

/** Partner values for a year, largest first, with shares of the reporter's total (mapped + unmapped). */
export function partnerShares(slice: TradeSlice, year: number): { shares: PartnerShare[]; mappedUsd: number; unmappedUsd: number; totalUsd: number; unmappedShare: number | null } {
  const byPartner = new Map<string, number>();
  for (const f of slice.flows) {
    if (f.year === year && isFiniteNumber(f.valueUsd) && f.valueUsd > 0) byPartner.set(f.partner, (byPartner.get(f.partner) ?? 0) + f.valueUsd);
  }
  const mappedUsd = [...byPartner.values()].reduce((s, v) => s + v, 0);
  const unmappedUsd = Math.max(0, slice.unmappedUsd[year] ?? 0);
  const totalUsd = mappedUsd + unmappedUsd;
  const shares = [...byPartner.entries()]
    .map(([partner, valueUsd]) => ({ partner, valueUsd, share: totalUsd > 0 ? valueUsd / totalUsd : 0 }))
    .sort((a, b) => b.valueUsd - a.valueUsd);
  return { shares, mappedUsd, unmappedUsd, totalUsd, unmappedShare: totalUsd > 0 ? unmappedUsd / totalUsd : null };
}

/**
 * Herfindahl–Hirschman index of the partner mix on a 0..1 scale (sum of squared shares of
 * the *mapped* partners, renormalised to mapped value). Null with no mapped value. Unmapped
 * trade is reported separately, never folded into a partner.
 */
export function hhi(shares: readonly { valueUsd: number }[]): number | null {
  const total = shares.reduce((s, x) => s + (x.valueUsd > 0 ? x.valueUsd : 0), 0);
  if (!(total > 0)) return null;
  return shares.reduce((s, x) => s + (x.valueUsd > 0 ? (x.valueUsd / total) ** 2 : 0), 0);
}

export function concentrationTrend(slice: TradeSlice): { year: number; hhi: number; unmappedShare: number | null; totalUsd: number }[] {
  const years = [...new Set(slice.flows.map((f) => f.year))].sort((a, b) => a - b);
  const out: { year: number; hhi: number; unmappedShare: number | null; totalUsd: number }[] = [];
  for (const year of years) {
    const p = partnerShares(slice, year);
    const h = hhi(p.shares);
    if (h !== null) out.push({ year, hhi: h, unmappedShare: p.unmappedShare, totalUsd: p.totalUsd });
  }
  return out;
}

/** Year-on-year growth of the total (mapped + unmapped) for the last two available years. */
export function latestGrowth(slice: TradeSlice): { year: number; growth: number } | null {
  const trend = concentrationTrend(slice);
  if (trend.length < 2) return null;
  const a = trend[trend.length - 2];
  const b = trend[trend.length - 1];
  return a.totalUsd > 0 ? { year: b.year, growth: b.totalUsd / a.totalUsd - 1 } : null;
}

// ---------------------------------------------------------------------------------------
// Exposure mapping
// ---------------------------------------------------------------------------------------

export const EXPOSURE_KIND_LABELS: Record<ExposureKind, string> = {
  reported: "Reported",
  estimated: "Estimated",
  user_assigned: "Owner-assigned",
};

export type ExposureSummary = {
  byTarget: { target: string; targetName: string; weight: number; kinds: ExposureKind[] }[];
  byKind: Record<ExposureKind, number>;
  /** Sum of all mapped weights. */
  mapped: number;
  /** 1 − mapped, never negative: the explicit remainder. */
  unknown: number;
  /** True when the weights sum above 100%: a data error to surface, not to scale away. */
  overAllocated: boolean;
};

export function exposureSummary(map: Pick<HoldingExposureMap, "weights">): ExposureSummary {
  const byKind: Record<ExposureKind, number> = { reported: 0, estimated: 0, user_assigned: 0 };
  const targets = new Map<string, { target: string; targetName: string; weight: number; kinds: Set<ExposureKind> }>();
  let mapped = 0;
  for (const w of map.weights) {
    if (!isFiniteNumber(w.weight) || w.weight <= 0) continue;
    mapped += w.weight;
    byKind[w.kind] += w.weight;
    const t = targets.get(w.target) ?? { target: w.target, targetName: w.targetName, weight: 0, kinds: new Set<ExposureKind>() };
    t.weight += w.weight;
    t.kinds.add(w.kind);
    targets.set(w.target, t);
  }
  return {
    byTarget: [...targets.values()]
      .sort((a, b) => b.weight - a.weight)
      .map((t) => ({ ...t, kinds: [...t.kinds] })),
    byKind,
    mapped,
    unknown: Math.max(0, 1 - mapped),
    overAllocated: mapped > 1 + 1e-6,
  };
}

/** The exposure of a mapped holding to a target, or 0 when the holding has none mapped. */
export function exposureTo(summary: ExposureSummary, target: string): number {
  return summary.byTarget.find((t) => t.target === target)?.weight ?? 0;
}

export type ExposureEffect =
  | { kind: "none"; text: string }
  | { kind: "unknown_only"; text: string }
  | { kind: "mapped"; weight: number; kinds: ExposureKind[]; text: string };

/**
 * The "how this could affect this holding" sentence for a country card. It exists only when
 * a mapped weight exists: a holding with no mapped exposure gets an explicit "no effect
 * inferred" line, and a wholly-unmapped holding says the exposure is unknown.
 */
export function exposureEffect(summary: ExposureSummary, target: string, targetName: string): ExposureEffect {
  const w = exposureTo(summary, target);
  if (w > 0) {
    const kinds = summary.byTarget.find((t) => t.target === target)!.kinds;
    const basis = kinds.map((k) => EXPOSURE_KIND_LABELS[k].toLowerCase()).join(" and ");
    return {
      kind: "mapped",
      weight: w,
      kinds,
      text: `${Math.round(w * 100)}% of this holding is mapped to ${targetName} (${basis}); a change here applies to that share only.`,
    };
  }
  if (summary.mapped === 0) {
    return { kind: "unknown_only", text: `No exposure is mapped for this holding, so its sensitivity to ${targetName} is unknown. No effect is inferred.` };
  }
  return {
    kind: "none",
    text: `No mapped exposure to ${targetName}; ${Math.round(summary.unknown * 100)}% of the holding is unmapped, so this is not proof of zero exposure.`,
  };
}
