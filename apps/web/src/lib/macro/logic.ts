import { countryName, resolveGeography } from "@/lib/charts/geo";
import { isFiniteNumber } from "@/lib/charts/scale";

import type {
  BasisLabel,
  Claim,
  ComparisonPoint,
  CountryExposureRow,
  EffectRow,
  ExposureBasis,
  ExposureMapping,
  IndicatorPoint,
  PortfolioCountryContext,
  Range,
  ResearchContext,
  TradeRow,
} from "./types";

const EPS = 1e-9;

export const BASIS_LABELS: Record<BasisLabel, string> = {
  reported: "Reported",
  estimated: "Estimated",
  user_assigned: "Owner-assigned",
  unclassified: "Unclassified",
};

export const BASIS_HINTS: Record<BasisLabel, string> = {
  reported: "Taken from the company's own disclosure",
  estimated: "Allocated by a stated method; not disclosed",
  user_assigned: "The owner's own assumption",
  unclassified: "The backend sent a basis this view does not know",
};

// ---------------------------------------------------------------------------------------
// Indicators
// ---------------------------------------------------------------------------------------

/** Position of `value` in a range as 0..1; null for a missing value or a degenerate range. Outside values clamp. */
export function rangePosition(value: number | null, range: Range | null): number | null {
  if (!isFiniteNumber(value) || !range) return null;
  const span = range.high - range.low;
  if (!(span > 0)) return null;
  return Math.min(1, Math.max(0, (value - range.low) / span));
}

/** Whether a value lies outside the stored range (it should not: the range includes the latest value). */
export function outsideRange(value: number | null, range: Range | null): boolean {
  return isFiniteNumber(value) && range !== null && (value < range.low - EPS || value > range.high + EPS);
}

/** Latest minus previous period, in the series' unit; null unless both exist. */
export function pointChange(p: Pick<IndicatorPoint, "value" | "previousValue">): number | null {
  return isFiniteNumber(p.value) && isFiniteNumber(p.previousValue) ? p.value - p.previousValue : null;
}

export type IndicatorGroup = { geography: string; name: string; points: IndicatorPoint[] };

/** Indicators grouped by geography (largest group first, then code), each ordered by series id. */
export function groupByGeography(points: readonly IndicatorPoint[]): IndicatorGroup[] {
  const map = new Map<string, IndicatorPoint[]>();
  // DE, DEU and 276 are one country: one group, not three headings called "Germany".
  for (const p of points) {
    const key = canonicalGeography(p.geography);
    map.set(key, [...(map.get(key) ?? []), p]);
  }
  return [...map.entries()]
    .map(([geography, list]) => ({
      geography,
      name: geographyName(geography),
      points: [...list].sort((a, b) => a.seriesId.localeCompare(b.seriesId) || a.source.localeCompare(b.source)),
    }))
    .sort((a, b) => b.points.length - a.points.length || a.geography.localeCompare(b.geography));
}

/** The alpha-2 (or "EU") code a spelling resolves to; an unrecognised spelling stays as given (upper-cased). */
export function canonicalGeography(code: string): string {
  const g = resolveGeography(code);
  return g.code ?? code.trim().toUpperCase();
}

/** "DE" -> "Germany"; an unrecognised code stays as given. */
export function geographyName(code: string): string {
  const g = resolveGeography(code);
  return g.code ? countryName(g.code) : code;
}

// ---------------------------------------------------------------------------------------
// Cross-country comparison (ranked, never a slope: the API gives one latest value per country)
// ---------------------------------------------------------------------------------------

export type ComparisonGroup = {
  key: string;
  seriesId: string;
  source: string;
  unit: string;
  /** Countries with a value, highest first (ties by code). */
  rows: (ComparisonPoint & { value: number })[];
  /** Countries returned without a value. */
  missing: string[];
  /** True when the countries' latest periods differ: the comparison is not like for like in time. */
  mixedPeriods: boolean;
  periodMin: string | null;
  periodMax: string | null;
};

/** One group per series (and source and unit); a unit is never mixed inside a group. */
export function comparisonGroups(points: readonly ComparisonPoint[]): ComparisonGroup[] {
  const map = new Map<string, ComparisonPoint[]>();
  for (const p of points) {
    const key = `${p.seriesId}|${p.source}|${p.unit}`;
    map.set(key, [...(map.get(key) ?? []), p]);
  }
  return [...map.entries()]
    .map(([key, list]) => {
      const rows = list
        .filter((p): p is ComparisonPoint & { value: number } => isFiniteNumber(p.value))
        .sort((a, b) => b.value - a.value || a.geography.localeCompare(b.geography));
      const periods = rows.map((r) => r.period).sort();
      return {
        key,
        seriesId: list[0].seriesId,
        source: list[0].source,
        unit: list[0].unit,
        rows,
        missing: list.filter((p) => !isFiniteNumber(p.value)).map((p) => p.geography),
        mixedPeriods: new Set(periods).size > 1,
        periodMin: periods[0] ?? null,
        periodMax: periods[periods.length - 1] ?? null,
      };
    })
    .sort((a, b) => b.rows.length - a.rows.length || a.seriesId.localeCompare(b.seriesId));
}

// ---------------------------------------------------------------------------------------
// Mapped exposure of one holding
// ---------------------------------------------------------------------------------------

export type MappedTarget = { key: string; kind: "geography" | "hs_product"; weight: number; bases: BasisLabel[]; segments: string[] };

/** Effects folded by geography or HS code; weights add up per target, bases stay listed apart. */
export function mappedTargets(effects: readonly EffectRow[]): MappedTarget[] {
  const map = new Map<string, MappedTarget>();
  for (const e of effects) {
    if (!isFiniteNumber(e.weight) || e.weight <= 0) continue;
    const kind = e.geography ? "geography" : e.hsCode ? "hs_product" : null;
    const key = e.geography ?? e.hsCode;
    if (!kind || !key) continue;
    const id = `${kind}:${key}`;
    const t = map.get(id) ?? { key, kind, weight: 0, bases: [], segments: [] };
    t.weight += e.weight;
    if (!t.bases.includes(e.basis)) t.bases.push(e.basis);
    if (!t.segments.includes(e.segment)) t.segments.push(e.segment);
    map.set(id, t);
  }
  return [...map.values()].sort((a, b) => b.weight - a.weight || a.key.localeCompare(b.key));
}

// ---------------------------------------------------------------------------------------
// Portfolio country exposure -> choropleth data
// ---------------------------------------------------------------------------------------

export type BasisFilter = "all" | ExposureBasis;

export type CountryExposureDatum = {
  /** Alpha-2 code, or "EU" for the aggregate. */
  code: string;
  /** Fraction of the portfolio's total value (the denominator), or null when it cannot be formed. */
  value: number | null;
  exposedValue: number | null;
  byBasis: Readonly<Record<string, number>>;
  holdings: number;
  /** True when several spellings (DE, DEU) were merged: the backend gives counts only, so `holdings` is a lower bound. */
  holdingsAtLeast: boolean;
};

export type CountryExposureView = {
  /** Countries that have a polygon. */
  countries: CountryExposureDatum[];
  /** The EU aggregate (no polygon). */
  aggregates: CountryExposureDatum[];
  /** Keys the code tables do not know: listed, never dropped. */
  unrecognised: CountryExposureRow[];
  /** Fraction of portfolio value with no active country mapping (the explicit remainder). */
  unmappedShare: number | null;
  unmappedValue: number | null;
  mappedShare: number | null;
  totalValue: number | null;
  /** Under a basis filter: share of portfolio value mapped on the chosen basis (all rows, drawn or not). Null under "all". */
  basisShare: number | null;
  /** Under a basis filter: share mapped on the other bases, which the countries shown do not include. Null under "all". */
  otherBasisShare: number | null;
};

/** The share an unrecognised-code row has on `basis` (its ratio under "all"), null without a denominator. */
export function rowShare(row: CountryExposureRow, basis: BasisFilter, total: number | null): number | null {
  if (basis === "all") return row.shareOfPortfolio.ratio;
  const den = row.shareOfPortfolio.denominator ?? total;
  if (!isFiniteNumber(den) || !(den > 0)) return null;
  return (row.byBasis[basis] ?? 0) / den;
}

function shareFor(row: Pick<CountryExposureDatum, "exposedValue" | "byBasis">, ratioAll: number | null, denominator: number | null, basis: BasisFilter): number | null {
  if (basis === "all") return ratioAll;
  if (!isFiniteNumber(denominator) || !(denominator > 0)) return null;
  return (row.byBasis[basis] ?? 0) / denominator;
}

/**
 * Folds the backend's country concentration into the choropleth input. `basis` filters to one
 * evidence basis: a country mapped only on another basis is a real zero here (mapped, none on
 * this basis), not a missing value. Rows whose codes collapse to one country (DE and DEU) add up.
 */
export function countryExposureView(ctx: PortfolioCountryContext, basis: BasisFilter): CountryExposureView {
  const empty: CountryExposureView = { countries: [], aggregates: [], unrecognised: [], unmappedShare: null, unmappedValue: null, mappedShare: null, totalValue: ctx.totalValue, basisShare: null, otherBasisShare: null };
  const c = ctx.country;
  if (!c) return empty;
  const merged = new Map<string, CountryExposureDatum & { ratioAll: number | null; denominator: number | null }>();
  let allBases = 0;
  let chosen = 0;
  const unrecognised: CountryExposureRow[] = [];
  for (const row of c.rows) {
    for (const [k, v] of Object.entries(row.byBasis)) {
      allBases += v;
      if (k === basis) chosen += v;
    }
    const g = resolveGeography(row.key);
    if (g.kind !== "country" && g.kind !== "aggregate") {
      unrecognised.push(row);
      continue;
    }
    const code = g.code as string;
    const prev = merged.get(code);
    const byBasis: Record<string, number> = { ...(prev?.byBasis ?? {}) };
    for (const [k, v] of Object.entries(row.byBasis)) byBasis[k] = (byBasis[k] ?? 0) + v;
    const ratio = row.shareOfPortfolio.ratio;
    merged.set(code, {
      code,
      value: null,
      exposedValue: sumOrNull(prev?.exposedValue, row.exposedValue),
      byBasis,
      holdings: Math.max(prev?.holdings ?? 0, row.holdings),
      holdingsAtLeast: prev !== undefined,
      ratioAll: sumOrNull(prev?.ratioAll, ratio),
      denominator: row.shareOfPortfolio.denominator ?? prev?.denominator ?? null,
    });
  }
  const list = [...merged.values()].map(({ ratioAll, denominator, ...d }) => ({
    ...d,
    // Under a basis filter the exposed value is that basis's value too, so value and share agree.
    exposedValue: basis === "all" ? d.exposedValue : (d.byBasis[basis] ?? 0),
    value: shareFor(d, ratioAll, denominator, basis),
  }));
  list.sort((a, b) => (b.value ?? -1) - (a.value ?? -1) || a.code.localeCompare(b.code));
  const total = ctx.totalValue;
  const den = isFiniteNumber(total) && total > 0 ? total : null;
  return {
    basisShare: basis === "all" || den === null ? null : chosen / den,
    otherBasisShare: basis === "all" || den === null ? null : Math.max(0, allBases - chosen) / den,
    countries: list.filter((d) => d.code !== "EU"),
    aggregates: list.filter((d) => d.code === "EU"),
    unrecognised,
    unmappedValue: c.unmappedValue,
    unmappedShare: isFiniteNumber(c.unmappedValue) && isFiniteNumber(total) && total > 0 ? c.unmappedValue / total : c.mapped.ratio === null ? null : Math.max(0, 1 - c.mapped.ratio),
    mappedShare: c.mapped.ratio,
    totalValue: total,
  };
}

function sumOrNull(a: number | null | undefined, b: number | null | undefined): number | null {
  if (!isFiniteNumber(b)) return isFiniteNumber(a) ? a : null;
  return (isFiniteNumber(a) ? a : 0) + b;
}

// ---------------------------------------------------------------------------------------
// Exposure mappings (the owner-editable table)
// ---------------------------------------------------------------------------------------

export function isActiveMapping(m: Pick<ExposureMapping, "validFrom" | "validTo">, today: string): boolean {
  return m.validFrom <= today && (m.validTo === null || m.validTo >= today);
}

export type DimensionSummary = { mapped: number; unknown: number; overAllocated: boolean; byBasis: Record<string, number>; count: number };

export type IssuerMappingSummary = {
  issuerId: number;
  geography: DimensionSummary;
  product: DimensionSummary;
  /** Mappings whose validity does not include today (expired or not yet valid). */
  inactive: number;
};

function emptyDimension(): DimensionSummary {
  return { mapped: 0, unknown: 1, overAllocated: false, byBasis: {}, count: 0 };
}

/**
 * Per issuer and per dimension (country, product), the weight active today and the unknown
 * remainder. The backend caps each dimension at 100% separately, so the two are never summed.
 */
export function summariseMappings(mappings: readonly ExposureMapping[], today: string): IssuerMappingSummary[] {
  const map = new Map<number, IssuerMappingSummary>();
  for (const m of mappings) {
    const s = map.get(m.issuerId) ?? { issuerId: m.issuerId, geography: emptyDimension(), product: emptyDimension(), inactive: 0 };
    map.set(m.issuerId, s);
    if (!isActiveMapping(m, today)) {
      s.inactive += 1;
      continue;
    }
    if (!isFiniteNumber(m.weight) || m.weight <= 0) continue;
    const dim = m.geography ? s.geography : m.hsCode ? s.product : null;
    if (!dim) continue;
    dim.mapped += m.weight;
    dim.count += 1;
    dim.byBasis[m.basis] = (dim.byBasis[m.basis] ?? 0) + m.weight;
  }
  for (const s of map.values()) {
    for (const dim of [s.geography, s.product]) {
      dim.unknown = Math.max(0, 1 - dim.mapped);
      dim.overAllocated = dim.mapped > 1 + 1e-6;
    }
  }
  return [...map.values()].sort((a, b) => a.issuerId - b.issuerId);
}

export type ExposureForm = {
  issuerId: string;
  segment: string;
  geography: string;
  hsCode: string;
  hsRevision: string;
  basis: ExposureBasis | "";
  /** Percent of the issuer, 0..100. */
  weightPercent: string;
  validFrom: string;
  validTo: string;
};

export type ExposureBody = {
  issuer_id: number;
  segment: string;
  geography: string | null;
  hs_code: string | null;
  hs_revision: string | null;
  basis: ExposureBasis;
  weight: number;
  valid_from: string;
  valid_to: string | null;
};

export type FormErrors = Partial<Record<keyof ExposureForm | "form", string>>;

/**
 * A geography typed into the editor, checked against the codes the app knows: the backend
 * accepts any 2 to 5 letters/digits, so a wrong code (UK, XK) would be stored as a mapping no
 * series ever matches. Alpha-3 and M49 spellings are accepted and stored as the alpha-2 code
 * (DEU and 276 become DE) so one country is never two keys.
 */
export function checkGeography(input: string): { code: string; error: null } | { code: null; error: string } {
  const text = input.trim().toUpperCase();
  if (text === "UK") return { code: null, error: "UK is not an ISO country code: the United Kingdom is GB." };
  const g = resolveGeography(text);
  if (g.kind === "country" || g.kind === "aggregate") return { code: g.code as string, error: null };
  if (g.kind === "world") return { code: null, error: "World is not a country; map an issuer to a country or to EU." };
  return { code: null, error: `${text} is not a country code this app knows (ISO alpha-2 such as DE, or EU). Kosovo (XK) has no code in the macro sources.` };
}

/** Client-side mirror of the backend's ExposureInput rules, so the obvious mistakes never leave the form. */
export function validateExposureForm(f: ExposureForm): { errors: FormErrors; body: ExposureBody | null } {
  const errors: FormErrors = {};
  const issuer = Number(f.issuerId);
  if (!/^\d+$/.test(f.issuerId.trim()) || !(issuer >= 1) || !Number.isSafeInteger(issuer)) errors.issuerId = "Enter the issuer id (a positive whole number).";
  const segment = f.segment.trim();
  if (segment.length < 1 || segment.length > 160) errors.segment = "Name the segment (1 to 160 characters).";
  const geography = f.geography.trim().toUpperCase();
  const hsCode = f.hsCode.trim();
  const hsRevision = f.hsRevision.trim();
  if (!geography && !hsCode) errors.form = "Give a geography or an HS product code (a mapping needs one of them).";
  let geographyCode = geography;
  if (geography && !/^[A-Z0-9]{2,5}$/.test(geography)) errors.geography = "Use 2 to 5 letters or digits, e.g. DE or US.";
  else if (geography) {
    const resolved = checkGeography(geography);
    if (resolved.error !== null) errors.geography = resolved.error;
    else geographyCode = resolved.code;
  }
  if (geography && hsCode) errors.form = "Map one dimension per row: either a geography or an HS product, not both.";
  if (hsCode && !/^[0-9]{2,6}$/.test(hsCode)) errors.hsCode = "An HS code is 2 to 6 digits.";
  if (hsCode && !hsRevision) errors.hsRevision = "A product mapping needs its HS revision, e.g. HS2022.";
  if (!hsCode && hsRevision) errors.hsRevision = "A revision only goes with an HS code.";
  if (!f.basis) errors.basis = "Choose reported, estimated or owner-assigned.";
  const weightText = f.weightPercent.trim();
  // Plain decimals only: Number() would also accept "0x10" (16) and "1e1" (10).
  const pct = /^\d+(\.\d+)?$/.test(weightText) ? Number(weightText) : Number.NaN;
  if (!Number.isFinite(pct) || pct < 0 || pct > 100) errors.weightPercent = "Enter a percentage from 0 to 100, as plain digits (e.g. 12.5).";
  else if (pct > 0 && Math.round(pct * 1e4) === 0) errors.weightPercent = "That weight is below 0.0001% and would be stored as zero. Enter 0 or a larger weight.";
  if (!/^\d{4}-\d{2}-\d{2}$/.test(f.validFrom)) errors.validFrom = "Choose the date the mapping starts.";
  if (f.validTo && !/^\d{4}-\d{2}-\d{2}$/.test(f.validTo)) errors.validTo = "Use a calendar date.";
  if (f.validTo && f.validFrom && f.validTo < f.validFrom) errors.validTo = "The end date is before the start date.";
  if (Object.keys(errors).length > 0) return { errors, body: null };
  return {
    errors,
    body: {
      issuer_id: issuer,
      segment,
      geography: geographyCode || null,
      hs_code: hsCode || null,
      hs_revision: hsCode ? hsRevision : null,
      basis: f.basis as ExposureBasis,
      // Percent -> fraction, rounded to 6 places so 12.5 stays 0.125 and not 0.12500000000000003.
      weight: Math.round(pct * 1e4) / 1e6,
      valid_from: f.validFrom,
      valid_to: f.validTo || null,
    },
  };
}

/** The messages of a FastAPI error body: a string `detail` (HTTPException) or a list of validation errors. */
export function apiErrorMessages(error: unknown): string[] {
  if (typeof error === "string" && error) return [error];
  if (typeof error !== "object" || error === null || !("detail" in error)) return [];
  const detail = (error as { detail: unknown }).detail;
  if (typeof detail === "string") return [detail];
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      if (typeof d === "string") return d;
      if (typeof d === "object" && d !== null) {
        const msg = "msg" in d && typeof d.msg === "string" ? d.msg : "Invalid value";
        const loc = "loc" in d && Array.isArray(d.loc) ? d.loc.filter((p: unknown) => p !== "body").join(".") : "";
        return loc ? `${loc}: ${msg}` : msg;
      }
      return "Invalid value";
    });
  }
  return [];
}

// ---------------------------------------------------------------------------------------
// Trade claims
// ---------------------------------------------------------------------------------------

/**
 * Reads the natural key of a trade claim from its id: `trade:{reporter}:{partner}:{flow}:{revision}:{hs}:{period}`
 * (research.py builds it from the stored row, every part reduced to [A-Za-z0-9.%+-_]). A claim whose id does not
 * have those seven parts is not a trade row this view can place, and is returned as null instead of guessed.
 */
export function parseTradeClaim(claim: Claim): TradeRow | null {
  if (claim.domain !== "trade") return null;
  const parts = claim.claimId.split(":");
  if (parts.length !== 7 || parts[0] !== "trade") return null;
  const [, reporter, partner, flow, hsRevision, hsCode, period] = parts;
  if (!reporter || !partner || !flow || !hsRevision || !hsCode || !/^\d{4}-\d{2}-\d{2}$/.test(period)) return null;
  const valueUsd = claim.value !== null && (claim.unit === "USD" || claim.currency === "USD") ? claim.value : null;
  return { claim, reporter, partner, flow, hsRevision, hsCode, period, valueUsd };
}

export function tradeRows(ctx: ResearchContext): { rows: TradeRow[]; unparsed: Claim[] } {
  const rows: TradeRow[] = [];
  const unparsed: Claim[] = [];
  for (const c of ctx.claims) {
    if (c.domain !== "trade") continue;
    const r = parseTradeClaim(c);
    if (r) rows.push(r);
    else unparsed.push(c);
  }
  return { rows, unparsed };
}

/** Human name of a Comtrade reporter or partner code (M49 number, alpha-2/3, World). */
export function tradePartyName(code: string): string {
  const g = resolveGeography(code);
  if (g.kind === "world") return "World";
  if (g.code) return countryName(g.code);
  return `Code ${code}`;
}

/**
 * The reporter of a trade query as the backend matches it: `/macro/trade` compares the text
 * exactly (case-sensitive, no alpha-3 or alias expansion) against the stored alpha-2 or Comtrade
 * M49 code, so "de", "DEU" and "276" would all read as "no data". Everything the code tables
 * know goes out as the alpha-2 code ("EU" for 97); anything else is refused with a reason.
 */
export function normaliseReporter(input: string): { code: string; error: null } | { code: null; error: string } {
  const text = input.trim();
  if (!/^[A-Za-z0-9]{2,5}$/.test(text)) return { code: null, error: "Reporter: 2 to 5 letters or digits, e.g. DE, US or 276." };
  const g = resolveGeography(text);
  if (g.kind === "country" || g.kind === "aggregate") return { code: g.code as string, error: null };
  if (g.kind === "world") return { code: null, error: "Reporter: World is not a reporter; enter a country." };
  return { code: null, error: `Reporter: ${text.toUpperCase()} is not an ISO country code or M49 number this app knows.` };
}

export function isWorld(code: string): boolean {
  return resolveGeography(code).kind === "world";
}

export type ProductSankey = {
  nodes: { id: string; label: string; column: 0 | 1 }[];
  links: { source: string; target: string; value: number }[];
  /** Rows drawn / rows of that flow in the response (World excluded from partners, listed apart). */
  drawn: number;
  worldRows: TradeRow[];
  period: string | null;
  revisions: string[];
};

/**
 * Product -> partner links of one flow from the response's rows. World is a total, not a partner,
 * so it is left out of the links and reported in `worldRows`. Rows missing a USD value are not
 * drawn. Several rows for the same product and partner (different revisions) add up only within
 * the same revision: a product node is "HS code (revision)".
 */
export function productSankey(rows: readonly TradeRow[], flow: string): ProductSankey {
  const ofFlow = rows.filter((r) => r.flow === flow);
  const nodes = new Map<string, { id: string; label: string; column: 0 | 1 }>();
  const links = new Map<string, { source: string; target: string; value: number }>();
  let drawn = 0;
  for (const r of ofFlow) {
    if (isWorld(r.partner) || !isFiniteNumber(r.valueUsd) || r.valueUsd <= 0) continue;
    const product = `hs:${r.hsCode}:${r.hsRevision}`;
    const partner = `partner:${r.partner}`;
    nodes.set(product, { id: product, label: `HS ${r.hsCode} (${r.hsRevision})`, column: 0 });
    nodes.set(partner, { id: partner, label: tradePartyName(r.partner), column: 1 });
    const key = `${product}>${partner}`;
    const l = links.get(key) ?? { source: product, target: partner, value: 0 };
    l.value += r.valueUsd;
    links.set(key, l);
    drawn += 1;
  }
  const periods = [...new Set(ofFlow.map((r) => r.period))].sort();
  return {
    nodes: [...nodes.values()],
    links: [...links.values()],
    drawn,
    worldRows: ofFlow.filter((r) => isWorld(r.partner)),
    period: periods[periods.length - 1] ?? null,
    revisions: [...new Set(ofFlow.map((r) => r.hsRevision))].sort(),
  };
}

export function tradeFlows(rows: readonly TradeRow[]): string[] {
  return [...new Set(rows.map((r) => r.flow))].sort();
}

/** Herfindahl (0..1) as the familiar 0..10,000 points. */
export function hhiPoints(hhi: number | null): number | null {
  return isFiniteNumber(hhi) ? Math.round(hhi * 10_000) : null;
}

/** "Equivalent to N equal partners" (1 / HHI), or null for a missing or zero index. */
export function equalPartners(hhi: number | null): number | null {
  return isFiniteNumber(hhi) && hhi > 0 ? 1 / hhi : null;
}

export const TRADE_METRIC_LABELS: Record<string, { label: string; kind: "growth" | "hhi" | "share" | "index"; flow?: string; note: string }> = {
  export_growth: { label: "Export growth", kind: "growth", flow: "export", note: "Latest period against the adjacent one only; all products stored for the reporter" },
  import_growth: { label: "Import growth", kind: "growth", flow: "import", note: "Latest period against the adjacent one only; all products stored for the reporter" },
  export_partner_concentration: { label: "Export partner concentration", kind: "hhi", flow: "export", note: "Herfindahl over country partners, World excluded; all products stored for the reporter" },
  import_partner_concentration: { label: "Import partner concentration", kind: "hhi", flow: "import", note: "Herfindahl over country partners, World excluded; all products stored for the reporter" },
  export_share_of_two_way_trade: { label: "Export share of two-way trade", kind: "share", note: "Exports over exports plus imports, latest common period; all products stored for the reporter" },
  product_mix_change: { label: "Product mix change", kind: "index", note: "Shift in the export product mix against the adjacent period; all products stored for the reporter" },
  revealed_trade_dependence: { label: "Revealed trade dependence", kind: "index", note: "Needs world totals and GDP; the backend does not compute it" },
};

/** A trade period older than this many months (UTC) is shown as stale rather than verified. */
export const TRADE_STALE_AFTER_MONTHS = 24;

/**
 * Whether the latest stored trade period is stale at `now`. National trade statistics lag by
 * months, so the threshold is deliberately generous: a period more than two years old reads
 * as stale, a recent one as verified. Null/unparseable periods are not judged (null).
 */
export function tradePeriodStale(period: string | null | undefined, now: Date): boolean | null {
  const m = period ? /^(\d{4})(?:-(\d{2}))?/.exec(period) : null;
  if (!m) return null;
  const year = Number(m[1]);
  const month = m[2] ? Number(m[2]) : 12;
  if (!Number.isFinite(year) || month < 1 || month > 12) return null;
  const months = (now.getUTCFullYear() - year) * 12 + (now.getUTCMonth() + 1 - month);
  return months > TRADE_STALE_AFTER_MONTHS;
}
