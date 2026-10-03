import type { components } from "@/lib/api/schema";
import { parseDataState, type DataState } from "@/lib/data-state/states";

import type {
  BasisLabel,
  ReleaseZoneSource,
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
  NextRelease,
  PortfolioConcentration,
  PortfolioCountryContext,
  PortfolioMacroCoverage,
  Range,
  ResearchContext,
  SeriesEffect,
  Share,
  TradeContext,
  TradeFlowPartners,
  TradePartnerPeriod,
  VintagePoint,
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
 * Backend availability strings -> the shared vocabulary, through the one adapter in
 * lib/data-state (so macro and coverage views agree on the same ledger row). Only the reason
 * is added here: the Eurostat history gap `vintage_unavailable` keeps the backend's wording,
 * and an unrecognised value is `unavailable` and says so, rather than being shown as healthy.
 */
export function toDataState(raw: string): { state: DataState; reason: string | null } {
  const key = raw.trim().toLowerCase();
  const state = parseDataState(key);
  if (state === null) return { state: "unavailable", reason: `Unrecognised source state "${raw}".` };
  if (key === "vintage_unavailable") return { state, reason: "Release history before collection began was not archived (vintage unavailable)." };
  return { state, reason: null };
}

function range(low: unknown, high: unknown): Range | null {
  const l = num(low);
  const h = num(high);
  return l !== null && h !== null && l <= h ? { low: l, high: h } : null;
}

// ---------------------------------------------------------------------------------------
// GET /macro/context/{ticker}
// ---------------------------------------------------------------------------------------

function parseVintage(v: Schemas["SeriesVintage"]): VintagePoint {
  const first = num(v.value_first_release);
  return {
    period: v.period,
    valueLatest: num(v.value_latest),
    valueFirstRelease: first,
    // Unavailable exactly when the first release is missing; the flag is trusted too.
    vintageUnavailable: v.vintage_unavailable === true || first === null,
    releasedAt: v.released_at ?? null,
    revisedAt: v.revised_at ?? null,
    revisionCount: v.revision_count,
  };
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}/;

function validZone(z: unknown): z is string {
  if (typeof z !== "string" || z === "") return false;
  try {
    new Intl.DateTimeFormat("en", { timeZone: z });
    return true;
  } catch {
    return false;
  }
}

type NextReleaseFields = Pick<
  Schemas["ContextPoint"],
  "next_release" | "next_release_time" | "next_release_source" | "next_release_estimate" | "next_release_timezone" | "next_release_timezone_source" | "next_release_basis"
>;

/**
 * Next release from the backend's fields. A dated release is a provider-calendar date or, when
 * the basis says `published_rule`, a date derived from the provider's own published rule; the
 * observed-cadence estimate is used only when no such date exists and is returned as an estimate,
 * never as a published date. A basis this client does not know degrades to unknown (nothing is
 * claimed). A zone is kept only with a time, only if Intl accepts it, and only with a recognised
 * source; otherwise the time is shown with no zone rather than with a guessed one.
 */
export function parseNextRelease(o: NextReleaseFields): NextRelease {
  const basis: unknown = o.next_release_basis;
  const known = basis === undefined || basis === null || basis === "provider_calendar" || basis === "published_rule" || basis === "observed_cadence";
  if (!known) return { kind: "unknown" };
  if (typeof o.next_release === "string" && ISO_DAY.test(o.next_release) && basis !== "observed_cadence") {
    const src = o.next_release_source;
    const time = typeof o.next_release_time === "string" && o.next_release_time !== "" ? o.next_release_time : null;
    const zoneSource: unknown = o.next_release_timezone_source;
    const zoneOk = time !== null && validZone(o.next_release_timezone) && (zoneSource === "stated_by_provider" || zoneSource === "agency_location");
    return {
      kind: basis === "published_rule" ? "rule" : "calendar",
      date: o.next_release.slice(0, 10),
      time,
      timezone: zoneOk ? (o.next_release_timezone as string) : null,
      timezoneSource: zoneOk ? (zoneSource as ReleaseZoneSource) : null,
      source: src && typeof src.url === "string" ? { provider: src.provider, url: src.url, title: src.title ?? null } : null,
    };
  }
  if (typeof o.next_release_estimate === "string" && ISO_DAY.test(o.next_release_estimate) && (basis === undefined || basis === null || basis === "observed_cadence")) {
    return { kind: "estimate", date: o.next_release_estimate.slice(0, 10) };
  }
  return { kind: "unknown" };
}

/** Vintage sources and the primary provider whose card carries their values as `history`. */
export const VINTAGE_PRIMARY_SOURCE: Readonly<Record<string, string>> = { alfred: "fred" };

/**
 * One card per series. The backend already folds a vintage source (ALFRED) into its primary
 * provider's card (the FRED card's `history` holds the first-release values), so a vintage row
 * normally never arrives. If one still does (an older backend), it is not a second card: it is
 * dropped when the primary card exists, after lending it its history when that card has none.
 * A vintage-only series (no primary card) keeps its own card.
 */
export function collapseVintageCards(points: readonly IndicatorPoint[]): IndicatorPoint[] {
  const keyOf = (p: IndicatorPoint) => `${p.seriesId}|${p.geography.trim().toUpperCase()}`;
  const primary = new Map<string, IndicatorPoint>();
  for (const p of points) if (!(p.source in VINTAGE_PRIMARY_SOURCE)) primary.set(`${p.source}|${keyOf(p)}`, p);
  const lent = new Map<string, readonly VintagePoint[]>();
  const kept: IndicatorPoint[] = [];
  for (const p of points) {
    const home = VINTAGE_PRIMARY_SOURCE[p.source];
    const card = home ? primary.get(`${home}|${keyOf(p)}`) : undefined;
    if (!home || !card) {
      kept.push(p);
      continue;
    }
    if (card.history.length === 0 && p.history.length > 0) lent.set(`${home}|${keyOf(p)}`, p.history);
  }
  return kept.map((p) => {
    const h = lent.get(`${p.source}|${keyOf(p)}`);
    return h ? { ...p, history: h } : p;
  });
}

/** Rows of a vintage source whose primary provider has the same series are the same series, not another one. */
export function dropVintageDuplicates<T extends { seriesId: string; source: string }>(rows: readonly T[]): T[] {
  return rows.filter((r) => {
    const home = VINTAGE_PRIMARY_SOURCE[r.source];
    return !home || !rows.some((o) => o.source === home && o.seriesId === r.seriesId);
  });
}

export function parseMacroContext(body: Schemas["MacroContext"] | unknown): Parsed<HoldingMacroContext> {
  if (!isRecord(body)) return fail("The macro context response was not an object.");
  const { observations, coverage, effects, comparisons } = body;
  if (![observations, coverage, effects, comparisons].every(Array.isArray)) return fail("The macro context response is missing a list.");
  const b = body as Schemas["MacroContext"];
  const asOf = str(b.as_of);
  if (!asOf) return fail("The macro context response has no as-of time.");
  return ok({
    asOf,
    observations: collapseVintageCards(b.observations.map((o): IndicatorPoint => {
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
        history: (o.history ?? []).map(parseVintage),
        releaseCalendar: o.release_calendar ?? null,
        nextRelease: parseNextRelease(o),
      };
    })),
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
    comparisons: dropVintageDuplicates(b.comparisons.map(
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
    )),
    exposureUnknown: b.exposure_unknown === true,
    seriesEffects: dropVintageDuplicates((b.series_effects ?? []).map(
      (s): SeriesEffect => ({
        seriesId: s.series_id,
        source: s.source,
        unit: s.unit,
        weightedChange: num(s.weighted_change),
        coveredWeight: num(s.covered_weight),
        geographies: s.geographies,
      }),
    )),
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
      issuerName: typeof r.issuer_name === "string" && r.issuer_name.trim() ? r.issuer_name : null,
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

function parsePartnerPeriod(p: Schemas["TradePartnerPeriod"]): TradePartnerPeriod {
  return {
    period: p.period,
    total: num(p.total),
    totalBasis: p.total_basis,
    mappedValue: num(p.mapped_value),
    unmappedValue: num(p.unmapped_value),
    unmappedShare: num(p.unmapped_share),
    partnerCount: p.partner_count,
    hhi: num(p.hhi),
    partnersExceedTotal: p.partners_exceed_total === true,
  };
}

function parseFlowPartners(f: Schemas["TradeFlowPartners"]): TradeFlowPartners {
  return {
    flow: f.flow,
    period: f.period,
    summary: parsePartnerPeriod(f.summary),
    partners: f.partners.map((p) => ({ partner: p.partner, partnerIso: p.partner_iso ?? null, value: num(p.value), share: num(p.share) })),
    history: f.history.map(parsePartnerPeriod),
  };
}

function numRecord(r: Record<string, unknown>): Record<string, number | null> {
  const out: Record<string, number | null> = {};
  for (const [k, v] of Object.entries(r)) out[k] = num(v);
  return out;
}

export function parseTradeContext(body: Schemas["TradeContext"] | unknown): Parsed<TradeContext> {
  if (!isRecord(body) || !isRecord(body.metrics)) return fail("The trade context was not in the expected shape.");
  const b = body as Schemas["TradeContext"];
  const latestByFlow: Record<string, number> = {};
  for (const [flow, v] of Object.entries(b.latest_value_by_flow ?? {})) {
    const n = num(v);
    if (n !== null) latestByFlow[flow] = n;
  }
  const metrics = numRecord(b.metrics);
  const rw = b.reporter_wide;
  if (rw && !isRecord(rw.metrics)) return fail("The reporter-wide trade block was malformed.");
  const flows = b.partners ?? [];
  if (!Array.isArray(flows) || flows.some((f) => !isRecord(f) || !isRecord(f.summary) || !Array.isArray(f.partners) || !Array.isArray(f.history))) return fail("The trade partner block was malformed.");
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
    reporterWide: rw ? { periods: rw.periods, hsRevision: rw.hs_revision, products: rw.products, metrics: numRecord(rw.metrics) } : null,
    partners: flows.map(parseFlowPartners),
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
