/*
 * Issuer / sector / geography exposure from GET /funds/aggregate: provenance, the nested
 * treemap hierarchy and the position -> fund -> issuer Sankey. All weights are shares of
 * TOTAL portfolio value. Cash, the funds' own cash/derivatives, funds not looked through and
 * the unknown remainder are separate nodes that are never redistributed over known issuers.
 */
import type { SankeyLinkInput, SankeyNodeInput } from "@/lib/charts/sankey";
import type { DataProvenance } from "@/lib/data-state/provenance";
import type { DataState } from "@/lib/data-state/states";

import type {
  AggregateReport,
  ExposureBucket,
  FundAsOf,
  FundOverlapReport,
  IssuerExposure,
} from "./types";

export type Dimension = "sector" | "region" | "country" | "currency";
export const DIMENSIONS: readonly Dimension[] = ["sector", "region", "country", "currency"];
export const DIMENSION_LABEL: Record<Dimension, string> = {
  sector: "Sector",
  region: "Region",
  country: "Country",
  currency: "Currency",
};

export const KEY_CASH = "__cash__";
export const KEY_FUND_CASH = "__fund_cash__";
export const KEY_UNKNOWN = "__unknown_fund_weight__";
export const KEY_NOT_LOOKED = "__not_looked_through__";
export const KEY_UNCLASSIFIED = "__unclassified__";

/** Weights below this are numerical noise, not exposure. */
export const EPS = 1e-6;

export const AGGREGATE_SOURCE = "Issuer-published fund holdings + portfolio snapshot (StewardAI look-through)";

/** Oldest holdings date among the looked-through funds (fund data are as of their own dates). */
export function oldestFundAsOf(funds: readonly FundAsOf[]): string | null {
  const real = funds.map((f) => f.holdings_as_of).filter((d): d is string => !!d).sort();
  return real[0] ?? null;
}

export function fundsLookedThrough(funds: readonly FundAsOf[]): { covered: number; total: number } {
  const held = funds.filter((f) => f.weight > EPS);
  return { covered: held.filter((f) => f.status === "ok" || f.status === "stale").length, total: held.length };
}

/**
 * The provenance every figure of the aggregate carries. `verified` only when every held fund
 * is looked through, none is stale and no fund weight is unknown.
 */
export function aggregateProvenance(report: AggregateReport): DataProvenance {
  const cov = fundsLookedThrough(report.funds);
  const unknown = report.unknown?.unknown_fund_weight ?? 0;
  const over = report.unknown?.over_reported_weight ?? 0;
  const overFunds = report.funds.filter((f) => f.reconciles === false && f.rows_total_weight !== null && f.rows_total_weight > 1 + EPS);
  let state: DataState = "verified";
  let reason: string | null = null;
  if (!report.available) {
    state = "not_covered";
    reason = report.notes[0] ?? "No portfolio snapshot is known at this date.";
  } else {
    // Every problem is reported, never just the first: a stale fund must not hide unknown weight.
    const reasons: string[] = [];
    if (report.stale_funds.length > 0) {
      state = "stale";
      reasons.push(`Stale fund holdings: ${report.stale_funds.join(", ")}.`);
    }
    if (cov.covered < cov.total || unknown > 0.0005) {
      if (state === "verified") state = "partial";
      reasons.push(`${(unknown * 100).toFixed(1)}% of portfolio value is unknown fund weight (not looked through or not accounted for by the published rows).`);
    }
    if (over > 0.0005 || overFunds.length > 0) {
      if (state === "verified") state = "partial";
      const names = overFunds.map((f) => f.label).join(", ");
      reasons.push(`Published rows of ${names || "a fund"} add up to more than 100% of its net assets${over > 0 ? ` (${(over * 100).toFixed(1)}% of portfolio value over-reported)` : ""}, so the buckets can total more than 100%.`);
    }
    reason = reasons.length > 0 ? reasons.join(" ") : null;
  }
  return {
    source: AGGREGATE_SOURCE,
    asOf: report.portfolio_snapshot_date ?? report.as_of,
    method: report.method,
    state,
    reason,
    coverage: cov.total > 0 ? { covered: cov.covered, total: cov.total, unit: "funds looked through" } : null,
  };
}

// ---------------------------------------------------------------------------------------------
// Nested treemap
// ---------------------------------------------------------------------------------------------

export type LeafKind = "issuer" | "remainder";
export type TreemapLeaf = { id: string; label: string; weight: number; kind: LeafKind; detail: string };
export type TreemapGroup = {
  key: string;
  label: string;
  kind: ExposureBucket["kind"];
  weight: number;
  /** Fund-held part and direct part of the group's weight. */
  directWeight: number;
  fundWeight: number;
  children: TreemapLeaf[];
  /** Named issuers add up to more than the bucket (rounding or an inconsistent report). */
  overflow: boolean;
  /**
   * The leaves were scaled down to the bucket weight: the backend adds each fund row to the
   * row's own bucket, but an issuer carries only its first-seen sector/country/currency, so an
   * issuer whose rows are classified differently is drawn whole in one bucket.
   */
  scaled: boolean;
};
export type ExposureTreemap = {
  dimension: Dimension;
  groups: TreemapGroup[];
  /** Sum of the drawn group weights (denominator of tile shares is TOTAL portfolio value, = 1). */
  drawn: number;
  reconciles: boolean;
  shownIssuers: number;
  /** Buckets with a zero/negative/non-finite weight that cannot be drawn. */
  dropped: number;
  /** Those buckets, with their signed weights (a negative fund derivative offset is real). */
  droppedBuckets: { label: string; weight: number }[];
};

function issuerKey(issuer: IssuerExposure, dim: Dimension): string {
  const v = dim === "sector" ? issuer.sector : dim === "region" ? issuer.region : dim === "country" ? issuer.country : issuer.currency;
  return v && v !== "" ? v : KEY_UNCLASSIFIED;
}

function finitePositive(n: number): boolean {
  return Number.isFinite(n) && n > EPS;
}

export function buildExposureTreemap(report: AggregateReport, dim: Dimension): ExposureTreemap | null {
  const view = report.exposures.find((e) => e.dimension === dim);
  if (!view) return null;
  const issuers = report.issuers.filter((i) => finitePositive(i.weight));
  const byKey = new Map<string, IssuerExposure[]>();
  for (const i of issuers) {
    const k = issuerKey(i, dim);
    byKey.set(k, [...(byKey.get(k) ?? []), i]);
  }
  let dropped = 0;
  const droppedBuckets: { label: string; weight: number }[] = [];
  const groups: TreemapGroup[] = [];
  for (const b of view.buckets) {
    if (!finitePositive(b.weight)) {
      dropped++;
      if (Number.isFinite(b.weight) && Math.abs(b.weight) > EPS) droppedBuckets.push({ label: b.label, weight: b.weight });
      continue;
    }
    const named = (byKey.get(b.key) ?? []).sort((a, c) => c.weight - a.weight);
    const isSpecial = b.kind !== "classified" && b.kind !== "unclassified";
    const namedTotal = named.reduce((s, i) => s + i.weight, 0);
    // An issuer is placed by its first-seen key, so its whole weight can exceed this bucket
    // while the rest of it sits in another bucket of the backend: cap the leaves at the bucket.
    const scale = !isSpecial && namedTotal > b.weight * (1 + 0.0005) + EPS ? b.weight / namedTotal : 1;
    const children: TreemapLeaf[] = named.map((i) => ({
      id: `${b.key}/${i.key}`,
      label: i.label,
      weight: i.weight * scale,
      kind: "issuer",
      detail: `${scale < 1 ? `issuer total ${(i.weight * 100).toFixed(2)}%, scaled to fit this bucket (part of it is classified in another bucket) · ` : ""}${i.merged_securities > 1 ? `${i.merged_securities} securities merged · ` : ""}direct ${(i.direct_weight * 100).toFixed(2)}% · via funds ${(i.fund_weight * 100).toFixed(2)}%`,
    }));
    const shown = namedTotal * scale;
    const rest = b.weight - shown;
    if (!isSpecial && rest > EPS) {
      children.push({
        id: `${b.key}/__rest__`,
        label: "Other issuers (outside the named top list)",
        weight: rest,
        kind: "remainder",
        detail: "Part of this bucket not attributed to a named issuer by this report.",
      });
    }
    groups.push({
      key: b.key,
      label: b.label,
      kind: b.kind,
      weight: b.weight,
      directWeight: b.direct_weight,
      fundWeight: b.fund_weight,
      children,
      overflow: !isSpecial && scale < 1,
      scaled: scale < 1,
    });
  }
  groups.sort((a, c) => specialRank(a.kind) - specialRank(c.kind) || c.weight - a.weight);
  if (groups.length === 0) return null;
  return {
    dimension: dim,
    groups,
    drawn: groups.reduce((s, g) => s + g.weight, 0),
    reconciles: view.reconciles,
    shownIssuers: issuers.length,
    dropped,
    droppedBuckets,
  };
}

/**
 * Plain-language caveats for the treemap: buckets that cannot be drawn (a negative fund
 * derivative offset), and the drawn total when it is not 100% (the tiles fill the canvas
 * whatever they sum to, so area is a share of the drawn total, not of the portfolio).
 */
export function treemapNotes(map: ExposureTreemap): string[] {
  const notes: string[] = [];
  for (const d of map.droppedBuckets) {
    notes.push(`${d.label}: ${(d.weight * 100).toFixed(2)}% of portfolio value cannot be drawn as a tile${d.weight < 0 ? " (a negative offset, for example a swap-based fund whose equity rows add up to more than its net assets)" : ""}.`);
  }
  if (Math.abs(map.drawn - 1) > 0.005) {
    notes.push(`The drawn tiles add up to ${(map.drawn * 100).toFixed(1)}% of portfolio value, not 100%; tile area is a share of that drawn total.`);
  }
  if (!map.reconciles) notes.push("The buckets do not add up to the total within tolerance; the difference is not drawn.");
  return notes;
}

function specialRank(kind: ExposureBucket["kind"]): number {
  // Classified buckets first, then the "not a real bucket" ones in a fixed order.
  switch (kind) {
    case "classified":
      return 0;
    case "unclassified":
      return 1;
    case "cash":
      return 2;
    case "fund_cash":
      return 3;
    case "not_looked_through":
      return 4;
    case "unknown_fund":
      return 5;
  }
}

// ---------------------------------------------------------------------------------------------
// Sankey: position -> fund holdings -> underlying issuer / remainder
// ---------------------------------------------------------------------------------------------

export type SankeyKind =
  | "position"
  | "direct_source"
  | "fund_source"
  | "cash_source"
  | "issuer"
  | "other_issuers"
  | "rest_of_fund"
  | "cash"
  | "fund_cash"
  | "unknown"
  | "not_looked_through";

export type LookThroughSankey = {
  nodes: SankeyNodeInput[];
  links: SankeyLinkInput[];
  kinds: Record<string, SankeyKind>;
  /** Weight drawn in the position column (= portfolio weight accounted for). */
  drawnTotal: number;
  /** 1 - drawnTotal: not drawn (negative derivative offsets, over-reported rows), shown in text. */
  gap: number;
  /** Named issuers drawn vs. all issuers in the report. */
  namedIssuers: number;
  notes: string[];
};

export const SANKEY_TOP_ISSUERS = 10;

/** An overlap report from another as-of date must not feed this date's aggregate (cash/derivative weights differ). */
export function usableOverlap(report: Pick<AggregateReport, "as_of">, overlap: FundOverlapReport | null): FundOverlapReport | null {
  return overlap && overlap.as_of === report.as_of ? overlap : null;
}

/**
 * One row per fund ISIN. The backend emits one FundAsOf per position ticker, so the same fund
 * held on two listings appears twice with the same ISIN; the flows and remainders are per ISIN,
 * so weights add up here (subtracting an ISIN's shown issuer weight once per row would lose flow).
 */
export function mergeFundsByIsin(funds: readonly FundAsOf[]): FundAsOf[] {
  const out = new Map<string, FundAsOf>();
  for (const f of funds) {
    const cur = out.get(f.isin);
    if (!cur) {
      out.set(f.isin, { ...f });
    } else {
      cur.weight += f.weight;
      cur.value_base += f.value_base;
      cur.unknown_weight += f.unknown_weight;
      cur.ticker = cur.ticker ?? f.ticker;
    }
  }
  return [...out.values()];
}

export function buildLookThroughSankey(
  report: AggregateReport,
  overlap: FundOverlapReport | null,
  topIssuers = SANKEY_TOP_ISSUERS,
): LookThroughSankey | null {
  if (!report.available) return null;
  const view = report.exposures[0];
  if (!view) return null;
  const nodes: SankeyNodeInput[] = [];
  const links: SankeyLinkInput[] = [];
  const kinds: Record<string, SankeyKind> = {};
  const notes: string[] = [];
  const addNode = (id: string, label: string, column: number, kind: SankeyKind) => {
    if (kinds[id]) return;
    nodes.push({ id, label, column });
    kinds[id] = kind;
  };
  const link = (source: string, target: string, value: number) => {
    if (Number.isFinite(value) && value > EPS) links.push({ source, target, value });
  };

  const cashW = view.buckets.find((b) => b.kind === "cash")?.weight ?? 0;
  const directTotal = view.buckets.filter((b) => b.kind !== "cash").reduce((s, b) => s + (b.direct_weight || 0), 0);
  const named = [...report.issuers].filter((i) => finitePositive(i.weight)).sort((a, c) => c.weight - a.weight).slice(0, topIssuers);

  // Column 0/1: positions and their look-through source.
  if (cashW > EPS) {
    addNode("pos:cash", "Cash", 0, "position");
    addNode("src:cash", "Cash (not a fund)", 1, "cash_source");
    link("pos:cash", "src:cash", cashW);
    addNode("out:cash", "Cash", 2, "cash");
    link("src:cash", "out:cash", cashW);
  }
  if (directTotal > EPS) {
    addNode("pos:direct", "Direct stocks", 0, "position");
    addNode("src:direct", "Direct holdings", 1, "direct_source");
    link("pos:direct", "src:direct", directTotal);
  }
  const fundRows = mergeFundsByIsin(report.funds);
  const coverageByIsin = new Map((overlap?.funds ?? []).map((f) => [f.isin, f]));
  for (const f of fundRows) {
    if (!finitePositive(f.weight)) continue;
    const short = f.ticker ?? f.label;
    addNode(`pos:${f.isin}`, short, 0, "position");
    const asOf = f.holdings_as_of ? ` · holdings ${f.holdings_as_of}${f.stale ? " (stale)" : ""}` : " · no holdings";
    addNode(`src:${f.isin}`, `${f.label}${asOf}`, 1, "fund_source");
    link(`pos:${f.isin}`, `src:${f.isin}`, f.weight);
  }

  // Column 2: named issuers.
  let shownDirect = 0;
  const shownByFund = new Map<string, number>();
  for (const i of named) {
    const id = `iss:${i.key}`;
    addNode(id, i.label, 2, "issuer");
    for (const c of i.channels) {
      if (c.kind === "direct") {
        link("src:direct", id, c.weight);
        shownDirect += c.weight;
      } else if (c.isin && kinds[`src:${c.isin}`]) {
        link(`src:${c.isin}`, id, c.weight);
        shownByFund.set(c.isin, (shownByFund.get(c.isin) ?? 0) + c.weight);
      }
    }
  }
  const restDirect = directTotal - shownDirect;
  if (restDirect > EPS) {
    addNode("out:other", "Other issuers (not individually shown)", 2, "other_issuers");
    link("src:direct", "out:other", restDirect);
  }

  // Per fund remainders: never merged into issuers, never redistributed.
  for (const f of fundRows) {
    if (!finitePositive(f.weight)) continue;
    const src = `src:${f.isin}`;
    if (f.status === "missing" || f.status === "not_configured") {
      addNode("out:not-looked", "Fund not looked through (unknown)", 2, "not_looked_through");
      link(src, "out:not-looked", f.weight);
      continue;
    }
    const cov = coverageByIsin.get(f.isin);
    const cashDerivative = cov ? f.weight * cov.cash_derivative_other_weight : null;
    if (f.unknown_weight > EPS) {
      addNode("out:unknown", "Unknown fund weight (rows do not account for it)", 2, "unknown");
      link(src, "out:unknown", f.unknown_weight);
    }
    const shown = shownByFund.get(f.isin) ?? 0;
    if (cashDerivative !== null && cashDerivative > EPS) {
      addNode("out:fund-cash", "Fund cash, derivatives & other", 2, "fund_cash");
      link(src, "out:fund-cash", cashDerivative);
    } else if (cashDerivative !== null && cashDerivative < -EPS) {
      notes.push(`${f.label}: negative derivative/other weight (${(cashDerivative * 100).toFixed(2)}% of portfolio) cannot be drawn as a flow.`);
    }
    const rest = f.weight - f.unknown_weight - Math.max(cashDerivative ?? 0, 0) - shown;
    if (rest > EPS) {
      if (cov) {
        addNode("out:other", "Other issuers (not individually shown)", 2, "other_issuers");
        link(src, "out:other", rest);
      } else {
        addNode("out:rest", "Rest of fund: other issuers, cash, derivatives (not split)", 2, "rest_of_fund");
        link(src, "out:rest", rest);
      }
    }
  }

  if (links.length === 0) return null;
  const drawnTotal = links.filter((l) => l.source.startsWith("pos:")).reduce((s, l) => s + l.value, 0);
  const gap = 1 - drawnTotal;
  if (Math.abs(gap) > 0.005) {
    notes.push(`The drawn flows account for ${(drawnTotal * 100).toFixed(1)}% of portfolio value; the remaining ${(gap * 100).toFixed(1)}% is not drawn (rounding, over-reported fund rows or an unpriced position).`);
  }
  // Dedupe merged links to the same endpoints (an issuer reached twice through one fund).
  const merged = new Map<string, SankeyLinkInput>();
  for (const l of links) {
    const k = `${l.source}>${l.target}`;
    const prev = merged.get(k);
    merged.set(k, prev ? { ...prev, value: prev.value + l.value } : { ...l });
  }
  return { nodes, links: [...merged.values()], kinds, drawnTotal, gap, namedIssuers: named.length, notes };
}

// ---------------------------------------------------------------------------------------------
// Fund-level helpers
// ---------------------------------------------------------------------------------------------

export function fundStatusLabel(f: Pick<FundAsOf, "status">): string {
  switch (f.status) {
    case "ok":
      return "Looked through";
    case "stale":
      return "Stale holdings";
    case "missing":
      return "No holdings snapshot";
    case "not_configured":
      return "No issuer mapping configured";
  }
}

export function fundDataState(f: Pick<FundAsOf, "status" | "reconciles">): DataState {
  if (f.status === "missing") return "unavailable";
  if (f.status === "not_configured") return "not_covered";
  if (f.status === "stale") return "stale";
  return f.reconciles === false ? "partial" : "verified";
}
