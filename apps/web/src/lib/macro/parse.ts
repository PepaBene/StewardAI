import type { components } from "@/lib/api/schema";
import { isDataState, type DataState } from "@/lib/data-state/states";

import type {
  BasisLabel,
  Claim,
  ClaimDomain,
  ComparisonPoint,
  CountryExposureRow,
  CoverageExposure,
  CoverageRow,
  EffectRow,
  ExposureMapping,
  HoldingMacroContext,
  IndicatorPoint,
  PortfolioConcentration,
  PortfolioCountryContext,
  PortfolioMacroCoverage,
  Range,
  ResearchContext,
  SeriesEffect,
  Share,
  TradeContext,
} from "./types";

/*
 * Parsers from the API bodies to the domain types. The macro endpoints are mostly typed in the
 * OpenAPI, but Decimal fields arrive as `number | string`, and a few objects are untyped
 * (`by_exposure` rows, trade `metrics`). Anything that does not have the expected shape makes
 * the whole body `unavailable` (a failed parse), never an empty or zero-filled result.
 */

type Schemas = components["schemas"];

export type Parsed<T> = { ok: true; value: T } | { ok: false; reason: string };

const ok = <T>(value: T): Parsed<T> => ({ ok: true, value });
const fail = <T>(reason: string): Parsed<T> => ({ ok: false, reason });

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** A decimal arriving as a number or a numeric string; anything else (null, "", NaN) is missing. */
export function num(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

export function toBasis(raw: unknown): BasisLabel {
  return raw === "reported" || raw === "estimated" || raw === "user_assigned" ? raw : "unclassified";
}

/**
 * Backend availability strings -> the shared vocabulary. "available" is "verified"; the
 * Eurostat history gap `vintage_unavailable` is partial coverage with the backend's reason; an
 * unrecognised value is `unavailable` and says so, rather than being shown as healthy.
 */
export function toDataState(raw: string): { state: DataState; reason: string | null } {
  const key = raw.trim().toLowerCase();
  if (key === "available" || key === "ok" || key === "verified") return { state: "verified", reason: null };
  if (key === "vintage_unavailable") return { state: "partial", reason: "Release history before collection began was not archived (vintage unavailable)." };
  if (isDataState(key)) return { state: key, reason: null };
  return { state: "unavailable", reason: `Unrecognised source state "${raw}".` };
}

function range(low: unknown, high: unknown): Range | null {
  const l = num(low);
  const h = num(high);
  return l !== null && h !== null && l <= h ? { low: l, high: h } : null;
}

// ---------------------------------------------------------------------------------------
// GET /macro/context/{ticker}
// ---------------------------------------------------------------------------------------

export function parseMacroContext(body: Schemas["MacroContext"] | unknown): Parsed<HoldingMacroContext> {
  if (!isRecord(body)) return fail("The macro context response was not an object.");
  const { observations, coverage, effects, comparisons } = body;
  if (![observations, coverage, effects, comparisons].every(Array.isArray)) return fail("The macro context response is missing a list.");
  const b = body as Schemas["MacroContext"];
  const asOf = str(b.as_of);
  if (!asOf) return fail("The macro context response has no as-of time.");
  return ok({
    asOf,
    observations: b.observations.map((o): IndicatorPoint => {
      const s = toDataState(o.access_state);
      return {
        seriesId: o.series_id,
        source: o.source,
        geography: o.geography,
        unit: o.unit,
        period: o.period,
        value: num(o.value),
        releaseAt: o.release_at,
        knownAt: o.known_at,
        sourceUrl: o.source_url,
        previousPeriod: o.previous_period ?? null,
        previousValue: num(o.previous_value),
        fiveYear: range(o.five_year_low, o.five_year_high),
        tenYear: range(o.ten_year_low, o.ten_year_high),
        state: s.state,
        stateReason: s.reason,
      };
    }),
    coverage: b.coverage.map((c): CoverageRow => {
      const s = toDataState(c.state);
      return { provider: c.provider, dataset: c.dataset, geography: c.geography, state: s.state, rawState: c.state, reason: c.reason ?? s.reason, checkedAt: c.checked_at };
    }),
    effects: b.effects.map(
      (e): EffectRow => ({
        ticker: e.ticker,
        issuerId: e.issuer_id,
        segment: e.segment,
        basis: toBasis(e.basis),
        weight: num(e.weight),
        geography: e.geography,
        hsCode: e.hs_code,
        unknownWeight: num(e.unknown_weight),
        explanation: e.explanation,
        dimension: e.dimension,
        relatedSeries: e.related_series,
      }),
    ),
    comparisons: b.comparisons.map(
      (c): ComparisonPoint => ({
        seriesId: c.series_id,
        source: c.source,
        geography: c.geography,
        unit: c.unit,
        period: c.period,
        value: num(c.value),
        releaseAt: c.release_at,
        knownAt: c.known_at,
        sourceUrl: c.source_url,
      }),
    ),
    exposureUnknown: b.exposure_unknown === true,
    seriesEffects: (b.series_effects ?? []).map(
      (s): SeriesEffect => ({
        seriesId: s.series_id,
        source: s.source,
        unit: s.unit,
        weightedChange: num(s.weighted_change),
        coveredWeight: num(s.covered_weight),
        geographies: s.geographies,
      }),
    ),
  });
}

// ---------------------------------------------------------------------------------------
// GET /portfolio/data-context (country and product concentration)
// ---------------------------------------------------------------------------------------

function parseShare(v: Schemas["Share"] | null | undefined): Share | null {
  if (!isRecord(v)) return null;
  return { numerator: num(v.numerator), denominator: num(v.denominator), ratio: num(v.ratio), denominatorBasis: str(v.denominator_basis) ?? "" };
}

function parseConcentration(c: Schemas["PortfolioConcentration"] | null | undefined): Parsed<PortfolioConcentration | null> {
  if (c === null || c === undefined) return ok(null);
  if (!isRecord(c) || !Array.isArray(c.rows)) return fail("The concentration block has no rows list.");
  const mapped = parseShare(c.mapped);
  if (!mapped) return fail("The concentration block has no mapped share.");
  const rows: CountryExposureRow[] = [];
  for (const r of c.rows) {
    const share = parseShare(r.share_of_portfolio);
    if (!share || !isRecord(r.by_basis)) return fail("A concentration row is malformed.");
    const byBasis: Record<string, number> = {};
    for (const [k, v] of Object.entries(r.by_basis)) {
      const n = num(v);
      if (n !== null) byBasis[k] = n;
    }
    rows.push({ key: r.key, exposedValue: num(r.exposed_value), shareOfPortfolio: share, shareOfMapped: parseShare(r.share_of_mapped), byBasis, holdings: r.holdings });
  }
  return ok({
    mapped,
    unmappedValue: num(c.unmapped_value),
    unmappedTickers: c.unmapped_tickers,
    rows,
    hhi: num(c.hhi),
    hhiDenominator: num(c.hhi_denominator),
    note: c.note,
  });
}

export function parsePortfolioCountryContext(body: Schemas["PortfolioDataContext"] | unknown): Parsed<PortfolioCountryContext> {
  if (!isRecord(body) || !Array.isArray(body.gaps)) return fail("The portfolio data context was not in the expected shape.");
  const b = body as Schemas["PortfolioDataContext"];
  const country = parseConcentration(b.country_concentration);
  if (!country.ok) return fail(country.reason);
  const product = parseConcentration(b.product_concentration);
  if (!product.ok) return fail(product.reason);
  return ok({
    snapshotDate: b.snapshot_date,
    knownAsOf: b.known_as_of,
    baseCurrency: b.base_currency,
    totalValue: num(b.total_value),
    positionCount: b.position_count,
    country: country.value,
    product: product.value,
    gaps: b.gaps.map((g) => ({ domain: g.domain, state: g.state, reason: g.reason })),
  });
}

// ---------------------------------------------------------------------------------------
// GET /macro/coverage/portfolio
// ---------------------------------------------------------------------------------------

function parseCoverageExposure(row: unknown): CoverageExposure | null {
  if (!isRecord(row)) return null;
  const type = row.exposure_type;
  const key = str(row.exposure_key);
  if ((type !== "geography" && type !== "hs_product") || !key) return null;
  return {
    exposureType: type,
    exposureKey: key,
    hsRevision: str(row.hs_revision),
    basis: toBasis(row.basis),
    mappedValue: num(row.mapped_value),
    holdingCount: num(row.holding_count),
    latestPeriod: str(row.latest_period),
    availableSeries: num(row.available_series),
  };
}

export function parsePortfolioMacroCoverage(body: Schemas["PortfolioMacroCoverage"] | unknown): Parsed<PortfolioMacroCoverage> {
  if (!isRecord(body) || !Array.isArray(body.by_exposure) || !Array.isArray(body.gaps)) return fail("The portfolio macro coverage was not in the expected shape.");
  const b = body as Schemas["PortfolioMacroCoverage"];
  const byExposure: CoverageExposure[] = [];
  for (const row of b.by_exposure) {
    const parsed = parseCoverageExposure(row);
    if (!parsed) return fail("A mapped-exposure row in the portfolio coverage is malformed.");
    byExposure.push(parsed);
  }
  return ok({
    asOf: b.as_of,
    baseCurrency: b.base_currency,
    totalValue: num(b.total_value),
    mappedValue: num(b.mapped_value),
    unknownValue: num(b.unknown_value),
    mappedWeight: num(b.mapped_weight),
    unknownWeight: num(b.unknown_weight),
    byExposure,
    gaps: b.gaps,
  });
}

// ---------------------------------------------------------------------------------------
// GET /macro/exposures
// ---------------------------------------------------------------------------------------

export function parseExposureMappings(body: Schemas["ExposureResult"][] | unknown): Parsed<ExposureMapping[]> {
  if (!Array.isArray(body)) return fail("The exposure mapping list was not an array.");
  const out: ExposureMapping[] = [];
  for (const r of body as Schemas["ExposureResult"][]) {
    if (!isRecord(r) || typeof r.id !== "number" || typeof r.issuer_id !== "number") return fail("An exposure mapping is malformed.");
    out.push({
      id: r.id,
      issuerId: r.issuer_id,
      segment: r.segment,
      geography: r.geography ?? null,
      hsRevision: r.hs_revision ?? null,
      hsCode: r.hs_code ?? null,
      basis: toBasis(r.basis),
      weight: num(r.weight),
      validFrom: r.valid_from,
      validTo: r.valid_to ?? null,
    });
  }
  return ok(out);
}

// ---------------------------------------------------------------------------------------
// GET /macro/trade/{reporter}/{hs_code}
// ---------------------------------------------------------------------------------------

export function parseTradeContext(body: Schemas["TradeContext"] | unknown): Parsed<TradeContext> {
  if (!isRecord(body) || !isRecord(body.metrics)) return fail("The trade context was not in the expected shape.");
  const b = body as Schemas["TradeContext"];
  const latestByFlow: Record<string, number> = {};
  for (const [flow, v] of Object.entries(b.latest_value_by_flow ?? {})) {
    const n = num(v);
    if (n !== null) latestByFlow[flow] = n;
  }
  const metrics: Record<string, number | null> = {};
  for (const [k, v] of Object.entries(b.metrics)) metrics[k] = num(v);
  return ok({
    reporter: b.reporter,
    hsCode: b.hs_code,
    hsRevision: b.hs_revision,
    partner: b.partner,
    periods: b.periods,
    latestPeriod: b.latest_period,
    latestByFlow,
    currency: b.latest_value_currency,
    metrics,
    sourceUrl: b.source_url,
    knownAt: b.known_at,
    notice: b.notice,
  });
}

// ---------------------------------------------------------------------------------------
// GET /research-context/country/{geography} and /industry/{hs_code}
// ---------------------------------------------------------------------------------------

const CLAIM_DOMAINS: readonly string[] = ["macro", "trade", "financial", "forward", "company_research", "business_network", "patents"];

export function parseResearchContext(body: Schemas["ResearchContext"] | unknown): Parsed<ResearchContext> {
  if (!isRecord(body) || !Array.isArray(body.claims) || !Array.isArray(body.gaps)) return fail("The research context was not in the expected shape.");
  const b = body as Schemas["ResearchContext"];
  const claims: Claim[] = [];
  for (const c of b.claims) {
    if (!CLAIM_DOMAINS.includes(c.domain)) return fail(`A claim has an unknown domain "${String(c.domain)}".`);
    claims.push({
      claimId: c.claim_id,
      domain: c.domain as ClaimDomain,
      subject: c.subject,
      metric: c.metric,
      label: c.label,
      value: num(c.value),
      textValue: c.text_value ?? null,
      unit: c.unit,
      currency: c.currency ?? null,
      period: c.period,
      knownAsOf: c.known_as_of,
      source: c.source,
      provenance: c.provenance,
      sourceUrl: c.source_url,
      note: c.note ?? null,
    });
  }
  return ok({ scope: b.scope, subject: b.subject, asOf: b.as_of, claims, gaps: b.gaps.map((g) => ({ domain: g.domain, reason: g.reason })), method: b.method });
}
