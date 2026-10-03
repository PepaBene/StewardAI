import { describe, expect, it } from "vitest";

import { num, parseExposureMappings, parseMacroContext, parsePortfolioCountryContext, parsePortfolioMacroCoverage, parseResearchContext, parseTradeContext, toBasis, toDataState } from "./parse";

describe("num", () => {
  it("parses decimals sent as numbers or strings", () => {
    expect(num(1.5)).toBe(1.5);
    expect(num("0.250000")).toBe(0.25);
    expect(num("-3")).toBe(-3);
  });
  it("treats everything else as missing, never zero", () => {
    for (const v of [null, undefined, "", "  ", "abc", Number.NaN, Infinity, {}, []]) expect(num(v)).toBeNull();
    expect(num(0)).toBe(0);
    expect(num("0")).toBe(0);
  });
});

describe("toDataState", () => {
  it("maps available to verified and passes the shared states through", () => {
    expect(toDataState("available")).toEqual({ state: "verified", reason: null });
    expect(toDataState("paid_only").state).toBe("paid_only");
    expect(toDataState("not_covered").state).toBe("not_covered");
  });
  it("maps the Eurostat history gap to partial coverage with a reason", () => {
    const s = toDataState("vintage_unavailable");
    expect(s.state).toBe("partial");
    expect(s.reason).toMatch(/not archived/);
  });
  it("never shows an unknown state as healthy", () => {
    const s = toDataState("weird");
    expect(s.state).toBe("unavailable");
    expect(s.reason).toMatch(/weird/);
  });
});

describe("toBasis", () => {
  it("keeps the three bases and marks anything else unclassified", () => {
    expect(toBasis("reported")).toBe("reported");
    expect(toBasis("user_assigned")).toBe("user_assigned");
    expect(toBasis("guess")).toBe("unclassified");
  });
});

const obs = {
  series_id: "cpi", source: "oecd", geography: "DE", value: "2.10", unit: "percent", period: "2026-08-31", release_at: "2026-09-12T08:00:00Z", known_at: "2026-09-12T09:00:00Z", source_url: "https://x.test",
  five_year_low: "-0.5", five_year_high: "9.0", ten_year_low: null, ten_year_high: null, access_state: "available", previous_period: "2026-07-31", previous_value: "2.4",
};

describe("parseMacroContext", () => {
  const body = {
    as_of: "2026-10-03T10:00:00Z",
    observations: [obs, { ...obs, series_id: "rate", value: null, access_state: "quota_exhausted", five_year_low: "3", five_year_high: "1" }],
    coverage: [{ provider: "oecd", dataset: "cpi", geography: "DE", state: "vintage_unavailable", checked_at: "2026-10-01T00:00:00Z", reason: "Eurostat release history was not archived before collection began" }],
    effects: [{ ticker: "SAP", issuer_id: 3, segment: "Cloud", basis: "estimated", weight: "0.4", geography: "DE", hs_code: null, unknown_weight: "0.6", related_series: ["cpi"], explanation: "e", dimension: "geography" }],
    comparisons: [{ series_id: "cpi", source: "oecd", geography: "US", value: "3.0", unit: "percent", period: "2026-08-31", release_at: null, known_at: "2026-09-12T09:00:00Z", source_url: "u" }],
    exposure_unknown: false,
    series_effects: [{ series_id: "cpi", source: "oecd", unit: "percent", weighted_change: "-0.12", covered_weight: "0.4", geographies: ["DE"] }],
  };
  it("parses decimals, ranges and states", () => {
    const r = parseMacroContext(body);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const [a, b] = r.value.observations;
    expect(a.value).toBe(2.1);
    expect(a.previousValue).toBe(2.4);
    expect(a.fiveYear).toEqual({ low: -0.5, high: 9 });
    expect(a.tenYear).toBeNull();
    expect(a.state).toBe("verified");
    expect(b.value).toBeNull();
    expect(b.state).toBe("quota_exhausted");
    expect(b.fiveYear).toBeNull(); // inverted range is not trusted
    expect(r.value.coverage[0].state).toBe("partial");
    expect(r.value.effects[0]).toMatchObject({ basis: "estimated", weight: 0.4, unknownWeight: 0.6 });
    expect(r.value.seriesEffects[0].weightedChange).toBe(-0.12);
    expect(r.value.comparisons[0].value).toBe(3);
  });
  it("rejects a body that is not in the expected shape instead of returning empties", () => {
    expect(parseMacroContext(null).ok).toBe(false);
    expect(parseMacroContext({ ...body, observations: "x" }).ok).toBe(false);
    expect(parseMacroContext({ ...body, as_of: undefined }).ok).toBe(false);
  });
  it("defaults missing series effects to none", () => {
    const { series_effects: _unused, ...rest } = body;
    void _unused;
    const r = parseMacroContext(rest);
    expect(r.ok && r.value.seriesEffects).toEqual([]);
  });
});

const share = (n: string, d: string, ratio: string | null) => ({ numerator: n, denominator: d, ratio, denominator_basis: "total portfolio value" });

describe("parsePortfolioCountryContext", () => {
  const conc = {
    dimension: "country", mapped: share("600", "1000", "0.6"), unmapped_value: "400", unmapped_tickers: ["AAA"],
    rows: [{ key: "US", exposed_value: "600", share_of_portfolio: share("600", "1000", "0.6"), share_of_mapped: share("600", "600", "1"), by_basis: { reported: "400", estimated: 200 }, holdings: 2 }],
    hhi: "1", hhi_denominator: "600", note: "n",
  };
  const body = { snapshot_date: "2026-10-01", known_as_of: "2026-10-03T00:00:00Z", base_currency: "CZK", total_value: "1000", position_count: 3, identity_coverage: null, country_concentration: conc, product_concentration: null, macro_sensitivities: [], adjusted_return_coverage: null, fund_overlap: null, fund_unknown: null, fund_total_value: null, gaps: [{ domain: "macro", state: "partial", reason: "r" }] };
  it("parses shares with their denominators and the basis split", () => {
    const r = parsePortfolioCountryContext(body);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.value.totalValue).toBe(1000);
    expect(r.value.country!.rows[0].byBasis).toEqual({ reported: 400, estimated: 200 });
    expect(r.value.country!.rows[0].shareOfPortfolio).toMatchObject({ ratio: 0.6, denominator: 1000 });
    expect(r.value.country!.hhi).toBe(1);
    expect(r.value.product).toBeNull();
  });
  it("accepts a portfolio with no country concentration", () => {
    const r = parsePortfolioCountryContext({ ...body, country_concentration: null });
    expect(r.ok && r.value.country).toBeNull();
  });
  it("rejects malformed concentration rows", () => {
    expect(parsePortfolioCountryContext({ ...body, country_concentration: { ...conc, rows: [{ key: "US" }] } }).ok).toBe(false);
    expect(parsePortfolioCountryContext({ ...body, gaps: undefined }).ok).toBe(false);
  });
});

describe("parsePortfolioMacroCoverage", () => {
  const body = {
    as_of: "2026-10-01", base_currency: "CZK", total_value: "1000", mapped_value: "600", unknown_value: "400", mapped_weight: "0.6", unknown_weight: "0.4",
    by_exposure: [{ exposure_type: "geography", exposure_key: "US", hs_revision: null, basis: "reported", mapped_value: "400", holding_count: 2, latest_period: "2026-06-30", available_series: 5 }],
    gaps: ["Some portfolio value has no active country/product exposure mapping."],
  };
  it("parses the untyped by_exposure rows", () => {
    const r = parsePortfolioMacroCoverage(body);
    expect(r.ok && r.value.byExposure[0]).toMatchObject({ exposureType: "geography", exposureKey: "US", mappedValue: 400, availableSeries: 5, basis: "reported" });
    expect(r.ok && r.value.unknownWeight).toBe(0.4);
  });
  it("rejects a row of an unknown exposure type rather than dropping it", () => {
    expect(parsePortfolioMacroCoverage({ ...body, by_exposure: [{ ...body.by_exposure[0], exposure_type: "region" }] }).ok).toBe(false);
    expect(parsePortfolioMacroCoverage({ ...body, by_exposure: [7] }).ok).toBe(false);
  });
});

describe("parseExposureMappings", () => {
  const m = { id: 1, issuer_id: 3, segment: "s", geography: "DE", hs_revision: null, hs_code: null, basis: "reported", weight: "0.5", valid_from: "2026-01-01", valid_to: null };
  it("parses the list", () => {
    const r = parseExposureMappings([m]);
    expect(r.ok && r.value[0]).toMatchObject({ id: 1, issuerId: 3, weight: 0.5, validTo: null });
  });
  it("accepts an empty list (nothing mapped is a valid state) and rejects non-arrays", () => {
    expect(parseExposureMappings([])).toEqual({ ok: true, value: [] });
    expect(parseExposureMappings({}).ok).toBe(false);
    expect(parseExposureMappings([{ ...m, id: "x" }]).ok).toBe(false);
  });
});

describe("parseTradeContext", () => {
  const body = { reporter: "US", hs_code: "8486", hs_revision: "HS2022", partner: null, periods: 4, latest_period: "2025-12-31", latest_value: "5", latest_value_by_flow: { export: "5000000", import: 1 }, latest_value_currency: "USD", metrics: { export_growth: "0.1", import_growth: null, revealed_trade_dependence: null }, source_url: null, known_at: "2026-02-01T00:00:00Z", notice: "n" };
  it("parses flows and metrics, keeping a null metric null", () => {
    const r = parseTradeContext(body);
    expect(r.ok && r.value.latestByFlow).toEqual({ export: 5_000_000, import: 1 });
    expect(r.ok && r.value.metrics).toEqual({ export_growth: 0.1, import_growth: null, revealed_trade_dependence: null });
  });
  it("rejects a body without metrics", () => {
    expect(parseTradeContext({ ...body, metrics: undefined }).ok).toBe(false);
  });
});

describe("parseResearchContext", () => {
  const claim = { claim_id: "macro:x", domain: "macro", subject: "DE", metric: "cpi", label: "cpi (DE)", value: "2.1", text_value: null, unit: "percent", currency: null, period: "2026-08-31", known_as_of: "2026-09-12", source: "oecd", provenance: "primary", source_url: null, note: "n" };
  const body = { scope: "country", subject: "DE", as_of: "2026-10-03T00:00:00Z", claims: [claim], gaps: [{ domain: "trade", reason: "none" }], method: "m" };
  it("parses claims and gaps", () => {
    const r = parseResearchContext(body);
    expect(r.ok && r.value.claims[0]).toMatchObject({ value: 2.1, domain: "macro", knownAsOf: "2026-09-12" });
    expect(r.ok && r.value.gaps).toEqual([{ domain: "trade", reason: "none" }]);
  });
  it("rejects an unknown claim domain", () => {
    expect(parseResearchContext({ ...body, claims: [{ ...claim, domain: "astrology" }] }).ok).toBe(false);
  });
  it("accepts a context with no claims, only gaps", () => {
    const r = parseResearchContext({ ...body, claims: [] });
    expect(r.ok && r.value.claims).toEqual([]);
  });
});
