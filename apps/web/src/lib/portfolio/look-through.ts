import type { components } from "@/lib/api/schema";
import { formatDay } from "@/lib/charts/time";

/*
 * ETF look-through (GET /portfolio/look-through): the portfolio with its funds expanded into
 * their holdings, from stored issuer snapshots. Pure display rules for the exposure panel,
 * the risk radar and the holdings table; every figure comes from the endpoint.
 */

type Schemas = components["schemas"];
export type LookThroughReport = Schemas["LookThroughReport"];
export type ExposureDimension = Schemas["ExposureDimension"];
export type ExposureBucket = Schemas["ExposureBucket"];
export type FundLookThrough = Schemas["FundLookThrough"];
export type UnderlyingHolding = Schemas["UnderlyingHolding"];
export type LookThroughBenchmark = Schemas["BenchmarkLookThrough"];
export type LookThroughConcentration = Schemas["Concentration"];

export type LookThroughDimension = ExposureDimension["dimension"];

export const LOOK_THROUGH_DIMENSIONS: readonly LookThroughDimension[] = ["country", "region", "sector", "currency"];

export const DIMENSION_TITLES: Record<LookThroughDimension, string> = {
  country: "Country",
  region: "Region",
  sector: "Sector",
  currency: "Currency",
};

/** True when at least one held fund is looked through (the toggle defaults on). */
export function hasLookThrough(report: LookThroughReport | null | undefined): report is LookThroughReport {
  return !!report && report.available && report.exposures.length > 0;
}

export function dimension(report: LookThroughReport, key: LookThroughDimension): ExposureDimension | undefined {
  return report.exposures.find((e) => e.dimension === key);
}

/**
 * Segments for a 100% bar: the classified buckets by weight (the chart folds the tail into
 * "Other"), then the special buckets (cash, the funds' own cash, funds not looked through,
 * unclassified) under their own names so nothing is spread over a bucket it wasn't
 * published for.
 */
export function exposureSegments(dim: ExposureDimension, slots = 8): { label: string; value: number }[] {
  const classified = dim.buckets.filter((b) => b.kind === "classified" && b.weight > 0);
  const special = dim.buckets.filter((b) => b.kind !== "classified" && b.weight > 0);
  const room = Math.max(1, slots - special.length);
  const fits = classified.length <= room;
  const head = (fits ? classified : classified.slice(0, room - 1)).map((b) => ({ label: b.label, value: b.weight }));
  const rest = fits ? [] : classified.slice(room - 1);
  const tail = rest.reduce((s, b) => s + b.weight, 0);
  return [
    ...head,
    ...(tail > 0 ? [{ label: `Other ${rest.length}`, value: tail }] : []),
    ...special.map((b) => ({ label: b.label, value: b.weight })),
  ];
}

/** The largest classified bucket and, when known, the benchmark's weight in it. */
export function largestBucket(dim: ExposureDimension): ExposureBucket | null {
  return dim.buckets.find((b) => b.kind === "classified") ?? null;
}

const PROVIDER: Record<string, string> = { ishares: "iShares", vanguard: "Vanguard" };

/**
 * "iShares, as of 25 Sep 2026" plus the fallback in use ("cached", "spreadsheet") — the
 * provenance line shown next to every look-through figure.
 */
export function fundProvenance(fund: FundLookThrough): string {
  const who = (fund.provider && PROVIDER[fund.provider]) ?? "Issuer";
  if (fund.status === "not_configured") return "No look-through source configured";
  if (!fund.as_of) return `${who}: no snapshot yet`;
  const parts = [`${who}, as of ${formatDay(fund.as_of)}`];
  if (fund.fallback === "spreadsheet") parts.push("full-fund download (no country data)");
  if (fund.fallback === "cached") parts.push("latest fetch failed, stored snapshot shown");
  if (fund.status === "stale") parts.push("stale");
  return parts.join(" · ");
}

/** The funds the portfolio holds, in the order the endpoint lists them. */
export function heldFunds(report: LookThroughReport): FundLookThrough[] {
  return report.funds.filter((f) => f.held);
}

/** Held funds by Trading212 ticker, for the holdings table's expandable ETF rows. */
export function fundsByTicker(report: LookThroughReport | null | undefined): Map<string, FundLookThrough> {
  return new Map((report?.funds ?? []).filter((f) => f.held && f.ticker).map((f) => [f.ticker!, f]));
}

/**
 * Look-through concentration on the radar: HHI on a fixed scale whose rim is HHI 0.10
 * ("as concentrated as 10 equal holdings"). The position-level scale (1/N to one position)
 * means nothing once a portfolio spans thousands of underlying names.
 */
export const LOOK_THROUGH_HHI_RIM = 0.1;

export function lookThroughConcentrationPosition(hhi: number): number {
  return Math.max(0, Math.min(1, hhi / LOOK_THROUGH_HHI_RIM));
}

/** The radar's look-through inputs (null when no held fund is looked through). */
export function lookThroughRisk(report: LookThroughReport | null | undefined) {
  if (!hasLookThrough(report)) return null;
  return {
    hhi: report.concentration?.hhi ?? null,
    benchmarkHhi: report.benchmark?.concentration?.hhi ?? null,
    fxShare: report.fx?.foreign_share ?? null,
    benchmarkFxShare: report.fx?.benchmark_foreign_share ?? null,
  };
}

let regionNames: Intl.DisplayNames | null = null;

/** "US" -> "United States" (English region names); the code itself when unknown, "—" for none. */
export function countryLabel(code: string | null | undefined): string {
  if (!code) return "—";
  try {
    regionNames ??= new Intl.DisplayNames(["en"], { type: "region" });
    return regionNames.of(code) ?? code;
  } catch {
    return code;
  }
}
