import { labelIfKey } from "@/lib/labels";
import { toNumber } from "@/lib/reference/peers";

/** One period of a macro series as the API returns it (decimals arrive as strings). */
export type SeriesPointInput = {
  period: string;
  first_value: string | number | null;
  first_known_at: string;
  latest_value: string | number | null;
  latest_known_at: string;
  release_at: string | null;
  vintages: number;
  revised: boolean;
};

export type SeriesInput = {
  source: string;
  series_id: string;
  geography: string;
  label: string | null;
  unit: string;
  frequency: string | null;
  seasonal_adjustment: string | null;
  first_basis: "provider_vintage" | "first_recorded";
  source_url: string | null;
  points: readonly SeriesPointInput[];
  revised_periods: number;
};

export type SeriesPoint = {
  period: string;
  first: number | null;
  latest: number | null;
  /** latest - first; null when either is unknown. */
  revision: number | null;
  vintages: number;
  revised: boolean;
  releaseAt: string | null;
};

export type SeriesView = {
  key: string;
  title: string;
  unit: string;
  source: string;
  geography: string;
  frequency: string | null;
  seasonalAdjustment: string | null;
  basis: SeriesInput["first_basis"];
  /** True when at least one period has more than one vintage, so first and latest can differ. */
  hasVintages: boolean;
  points: readonly SeriesPoint[];
  revisedPeriods: number;
  /** Mean absolute revision over periods that have both values; null when none do. */
  meanAbsRevision: number | null;
  /** Latest value change from the first charted period, in the series' own unit. */
  change: number | null;
  latestPeriod: string | null;
  sourceUrl: string | null;
};

export const BASIS_LABEL: Record<SeriesInput["first_basis"], string> = {
  provider_vintage: "First value is the provider's own earlier vintage",
  first_recorded: "First value is the first one this app recorded, which may be later than the original release",
};

export const BASIS_SHORT: Record<SeriesInput["first_basis"], string> = {
  provider_vintage: "Provider vintage",
  first_recorded: "First recorded",
};

export function seriesView(s: SeriesInput): SeriesView {
  const points: SeriesPoint[] = s.points.map((p) => {
    const first = toNumber(p.first_value);
    const latest = toNumber(p.latest_value);
    return {
      period: p.period,
      first,
      latest,
      revision: first !== null && latest !== null ? latest - first : null,
      vintages: p.vintages,
      revised: p.revised,
      releaseAt: p.release_at,
    };
  });
  const revs = points.flatMap((p) => (p.revision === null ? [] : [Math.abs(p.revision)]));
  const known = points.filter((p) => p.latest !== null);
  const change = known.length >= 2 ? known[known.length - 1].latest! - known[0].latest! : null;
  return {
    key: `${s.source}:${s.series_id}:${s.geography}`,
    title: labelIfKey("macro", s.label ?? s.series_id),
    unit: s.unit,
    source: s.source,
    geography: s.geography,
    frequency: s.frequency,
    seasonalAdjustment: s.seasonal_adjustment,
    basis: s.first_basis,
    hasVintages: points.some((p) => p.vintages > 1),
    points,
    revisedPeriods: s.revised_periods,
    meanAbsRevision: revs.length > 0 ? revs.reduce((a, b) => a + b, 0) / revs.length : null,
    change,
    latestPeriod: points.length > 0 ? points[points.length - 1].period : null,
    sourceUrl: s.source_url,
  };
}

export type SeriesOrder = "recent" | "revised" | "change";

/** Rank for comparison. A series with no figure for the chosen order goes last, never first. */
export function orderSeries(views: readonly SeriesView[], order: SeriesOrder): SeriesView[] {
  const score = (v: SeriesView): number | null =>
    order === "revised" ? v.meanAbsRevision : order === "change" ? (v.change === null ? null : Math.abs(v.change)) : null;
  const out = [...views];
  if (order === "recent") return out;
  return out.sort((a, b) => {
    const sa = score(a);
    const sb = score(b);
    if (sa === null && sb === null) return a.title.localeCompare(b.title);
    if (sa === null) return 1;
    if (sb === null) return -1;
    return sb - sa;
  });
}

/**
 * Rebase to 100 at the first charted period with a latest value, so series in different units
 * share one axis. Null when the base is missing or zero (the ratio would be meaningless).
 */
/** A view re-expressed on an index scale, with its change and revision statistics recomputed. */
export function indexView(v: SeriesView): SeriesView {
  const points = rebased(v.points);
  const revs = points.flatMap((p) => (p.revision === null ? [] : [Math.abs(p.revision)]));
  const known = points.filter((p) => p.latest !== null);
  return {
    ...v,
    unit: "index (first period = 100)",
    points,
    meanAbsRevision: revs.length > 0 ? revs.reduce((a, b) => a + b, 0) / revs.length : null,
    change: known.length >= 2 ? known[known.length - 1].latest! - known[0].latest! : null,
  };
}

/** Same rebasing applied to both the first and the latest value, so the revision gap survives. */
export function rebased(points: readonly SeriesPoint[]): SeriesPoint[] {
  const base = points.find((p) => p.latest !== null)?.latest ?? null;
  const scale = (n: number | null) => (n === null || base === null || base === 0 ? null : (n / base) * 100);
  return points.map((p) => {
    const first = scale(p.first);
    const latest = scale(p.latest);
    return { ...p, first, latest, revision: first !== null && latest !== null ? latest - first : null };
  });
}

export function indexed(points: readonly SeriesPoint[]): (number | null)[] {
  const base = points.find((p) => p.latest !== null)?.latest ?? null;
  if (base === null || base === 0) return points.map(() => null);
  return points.map((p) => (p.latest === null ? null : (p.latest / base) * 100));
}
