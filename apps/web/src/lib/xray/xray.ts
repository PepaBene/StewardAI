import type { components, paths } from "@/lib/api/schema";

type S = components["schemas"];
export type XrayData = S["XrayResponse"];
export type XrayView = S["XrayView"];
export type XrayBucket = S["XrayBucket"];
export type IntersectionData = S["XrayIntersection"];
export type RemovalData = S["XrayRemoval"];
export type ConcentrationData = S["XrayConcentrationResponse"];
export type ConcentrationLevel = S["XrayConcentration"];
export type FundamentalsData = S["XrayFundamentals"];
export type MetricData = S["XrayMetric"];
export type BenchmarkData = S["XrayBenchmark"];
export type BenchmarkDim = S["XrayBenchmarkDim"];
export type ActiveLine = S["XrayActive"];
export type ProposalData = S["XrayProposal"];
export type CandidateData = S["XrayCandidate"];
export type ProposedRequest =
  paths["/portfolio/xray/proposed"]["post"]["requestBody"]["content"]["application/json"];

/** The order the lens groups are shown in; `owner data` lenses are the ones that need your input. */
export const GROUP_ORDER = ["classification", "structure", "derived", "owner data"] as const;

const GROUP_LABEL: Record<string, string> = {
  classification: "Classification",
  structure: "Structure",
  derived: "Derived by StewardAI",
  "owner data": "Needs your data",
};

export function groupLabel(group: string): string {
  return GROUP_LABEL[group] ?? group;
}

export function groupViews(views: readonly XrayView[]): { group: string; label: string; views: XrayView[] }[] {
  return GROUP_ORDER.map((group) => ({
    group,
    label: groupLabel(group),
    views: views.filter((v) => v.group === group),
  })).filter((g) => g.views.length > 0);
}

/** Buckets that say what is NOT known or not a holding: never merged into "smaller". */
const SPECIAL_KINDS = new Set(["cash", "fund_cash", "not_looked_through", "unknown_fund", "unclassified"]);

export type Segment = {
  key: string;
  label: string;
  weight: number;
  kind: XrayBucket["kind"];
  /** Rank among the real (classified) segments, 0 = largest; -1 for the others. */
  rank: number;
};

/**
 * The segments of the composition strip: buckets in order, real buckets under `minShare` merged
 * into one trailing "Smaller" segment so the strip stays legible. Cash and every unknown bucket
 * keep their own segment however small, so missing knowledge is never absorbed into "smaller".
 */
export function compositionSegments(buckets: readonly XrayBucket[], minShare = 0.012): Segment[] {
  const real = buckets.filter((b) => !SPECIAL_KINDS.has(b.kind) && b.kind !== "other" && b.weight > 0);
  const bigKeys = new Set(real.filter((b) => b.weight >= minShare).map((b) => b.key));
  const out: Segment[] = [];
  let smaller = 0;
  let rank = 0;
  for (const b of buckets) {
    if (b.weight <= 0) continue;
    if (SPECIAL_KINDS.has(b.kind)) {
      out.push({ key: b.key, label: b.label, weight: b.weight, kind: b.kind, rank: -1 });
    } else if (b.kind === "other") {
      out.push({ key: b.key, label: b.label, weight: b.weight, kind: "other", rank: -1 });
    } else if (bigKeys.has(b.key)) {
      out.push({ key: b.key, label: b.label, weight: b.weight, kind: b.kind, rank: rank++ });
    } else {
      smaller += b.weight;
    }
  }
  if (smaller > 0) out.push({ key: "__smaller__", label: "Smaller buckets", weight: smaller, kind: "other", rank: -1 });
  // keep real segments first, then smaller, then the unknown and cash segments at the end
  const order = (s: Segment) => (s.kind === "classified" ? 0 : s.kind === "other" ? 1 : 2);
  return out.sort((a, b) => order(a) - order(b) || b.weight - a.weight);
}

const ACCENT_STEPS = ["bg-accent", "bg-accent/80", "bg-accent/62", "bg-accent/46", "bg-accent/32"] as const;

/** Fill for a segment: accent steps by rank, a hatch for unknown, flat neutrals for cash and the rest. */
export function segmentTone(segment: Pick<Segment, "kind" | "rank">): { className: string; hatched: boolean } {
  switch (segment.kind) {
    case "classified":
      return { className: ACCENT_STEPS[Math.min(Math.max(segment.rank, 0), ACCENT_STEPS.length - 1)], hatched: false };
    case "other":
      return { className: "bg-neutral-signal/45", hatched: false };
    case "cash":
    case "fund_cash":
      return { className: "bg-border", hatched: false };
    case "unclassified":
      return { className: "bg-neutral-signal/25", hatched: false };
    default:
      return { className: "hatch-unknown", hatched: true };
  }
}

export const BASIS_LABEL: Record<string, string> = {
  reported: "Reported",
  inferred: "Inferred",
  owner: "Your entry",
};

export const BASIS_HELP: Record<string, string> = {
  reported: "Published by the fund issuer, the instrument list or a data provider.",
  inferred: "Derived by StewardAI (a region from a country, a provider label mapped to GICS, a market-cap band).",
  owner: "Entered by you, in your settings or a segment mapping.",
};

/** How a bucket was classified, as shares of its classified weight, largest first. */
export function basisShares(basis: Record<string, number>): { basis: string; label: string; share: number }[] {
  const total = Object.values(basis).reduce((a, b) => a + b, 0);
  if (total <= 0) return [];
  return ["reported", "inferred", "owner"]
    .filter((k) => (basis[k] ?? 0) > 0)
    .map((k) => ({ basis: k, label: BASIS_LABEL[k] ?? k, share: (basis[k] ?? 0) / total }))
    .sort((a, b) => b.share - a.share);
}

export const COUNTRY_KIND_LABEL: Record<string, string> = {
  country_of_risk: "Country of risk",
  listing_country: "Listing country",
  home_market: "Home market (instrument list)",
  owner: "Your own entry",
  unstated: "Basis not stated",
};

/** "+3.1 pp" from a weight difference given as a fraction. */
export function pointsLabel(delta: number | null | undefined, decimals = 1): string {
  if (delta === null || delta === undefined || !Number.isFinite(delta)) return "—";
  const points = delta * 100;
  if (Math.abs(points) < 0.5 * 10 ** -decimals) return `${(0).toFixed(decimals)} pp`;
  return `${points > 0 ? "+" : "−"}${Math.abs(points).toFixed(decimals)} pp`;
}

/** A diverging bar for an active weight: which side of the benchmark and how wide (0-100). */
export function activeBar(active: number, maxAbs: number): { side: "over" | "under" | "none"; width: number } {
  if (!Number.isFinite(active) || active === 0 || maxAbs <= 0) return { side: "none", width: 0 };
  return { side: active > 0 ? "over" : "under", width: Math.min(100, (Math.abs(active) / maxAbs) * 100) };
}

/** Parse a typed weight in percent ("12.5" or "12,5") into a fraction 0-1, or null. */
export function weightToFraction(text: string): number | null {
  const t = text.trim().replace(",", ".");
  if (t === "" || !/^\d+(\.\d+)?$/.test(t)) return null;
  const n = Number(t);
  return n >= 0 && n <= 100 ? n / 100 : null;
}

export type EditRow = { ticker: string; current: number; text: string };
export type AddRow = { ticker: string; text: string };

export type BuiltProposal =
  | { ok: true; request: ProposedRequest; changed: number; invested: number }
  | { ok: false; error: string };

const EPS = 0.00005;

/**
 * The request for the proposal API from edited weights: only holdings whose typed weight differs
 * from the current one are sent, so an untouched holding keeps exactly its current weight. An
 * unreadable entry is an error naming the holding rather than being skipped.
 */
export function buildProposal(rows: readonly EditRow[], additions: readonly AddRow[]): BuiltProposal {
  const weights: ProposedRequest["weights"] = [];
  let invested = 0;
  for (const r of rows) {
    const typed = weightToFraction(r.text);
    if (typed === null) return { ok: false, error: `${r.ticker}: enter a weight between 0 and 100.` };
    invested += typed;
    if (Math.abs(typed - r.current) > EPS) weights.push({ ticker: r.ticker, weight: typed });
  }
  const adds: ProposedRequest["additions"] = [];
  for (const a of additions) {
    const typed = weightToFraction(a.text);
    if (typed === null || typed === 0) return { ok: false, error: `${a.ticker}: enter a weight above 0 and up to 100.` };
    invested += typed;
    adds.push({ ticker: a.ticker, weight: typed });
  }
  if (invested > 1 + EPS) {
    return { ok: false, error: `The weights add to ${(invested * 100).toFixed(1)}%; they cannot exceed 100%.` };
  }
  if (weights.length === 0 && adds.length === 0) return { ok: false, error: "Change a weight or add a security first." };
  return { ok: true, request: { weights, additions: adds }, changed: weights.length + adds.length, invested };
}

/** The share left as cash for the weights as typed (null when any entry is unreadable). */
export function cashRemainder(rows: readonly EditRow[], additions: readonly AddRow[]): number | null {
  let invested = 0;
  for (const r of rows) {
    const v = weightToFraction(r.text);
    if (v === null) return null;
    invested += v;
  }
  for (const a of additions) {
    const v = weightToFraction(a.text);
    if (v === null) return null;
    invested += v;
  }
  return 1 - invested;
}

/** The metric ids in the order the fundamentals page lists them. */
export const METRIC_ORDER = ["earnings_yield", "pe", "price_to_book", "dividend_yield", "profitability", "leverage"] as const;

export function coverageLabel(coverage: number | null | undefined): string {
  if (coverage === null || coverage === undefined) return "—";
  return `${Math.round(coverage * 100)}% of equity`;
}
