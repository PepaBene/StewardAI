import { describe, expect, it } from "vitest";

import {
  apiErrorMessages,
  comparisonGroups,
  countryExposureView,
  equalPartners,
  groupByGeography,
  hhiPoints,
  isActiveMapping,
  mappedTargets,
  outsideRange,
  parseTradeClaim,
  pointChange,
  productSankey,
  rangePosition,
  summariseMappings,
  tradePartyName,
  tradeRows,
  validateExposureForm,
  type ExposureForm,
} from "./logic";
import type { Claim, ComparisonPoint, CountryExposureRow, EffectRow, ExposureMapping, IndicatorPoint, PortfolioCountryContext, ResearchContext, Share } from "./types";

// ---- builders (inline: no shared fixtures) -------------------------------------------

const share = (numerator: number | null, denominator: number | null): Share => ({
  numerator,
  denominator,
  ratio: numerator !== null && denominator ? numerator / denominator : null,
  denominatorBasis: "total portfolio value",
});

function row(key: string, byBasis: Record<string, number>, total = 1000, holdings = 1): CountryExposureRow {
  const value = Object.values(byBasis).reduce((a, b) => a + b, 0);
  return { key, exposedValue: value, shareOfPortfolio: share(value, total), shareOfMapped: null, byBasis, holdings };
}

function ctx(rows: CountryExposureRow[], over: Partial<PortfolioCountryContext> = {}, unmapped = 100): PortfolioCountryContext {
  const mappedValue = rows.reduce((s, r) => s + (r.exposedValue ?? 0), 0);
  return {
    snapshotDate: "2026-10-01",
    knownAsOf: "2026-10-02T00:00:00Z",
    baseCurrency: "CZK",
    totalValue: 1000,
    positionCount: 4,
    country: { mapped: share(mappedValue, 1000), unmappedValue: unmapped, unmappedTickers: ["XYZ"], rows, hhi: 0.4, hhiDenominator: mappedValue, note: "n" },
    product: null,
    gaps: [],
    ...over,
  };
}

function point(over: Partial<IndicatorPoint> = {}): IndicatorPoint {
  return {
    seriesId: "gdp_growth",
    source: "world_bank",
    geography: "DE",
    unit: "percent",
    period: "2025-12-31",
    value: 1.2,
    releaseAt: null,
    knownAt: "2026-02-01T00:00:00Z",
    sourceUrl: "https://example.test",
    previousPeriod: "2024-12-31",
    previousValue: 0.4,
    fiveYear: { low: -3, high: 5 },
    tenYear: { low: -5, high: 6 },
    state: "verified",
    stateReason: null,
    ...over,
  };
}

const mapping = (over: Partial<ExposureMapping> = {}): ExposureMapping => ({
  id: 1,
  issuerId: 7,
  segment: "Data centre",
  geography: "US",
  hsRevision: null,
  hsCode: null,
  basis: "reported",
  weight: 0.4,
  validFrom: "2025-01-01",
  validTo: null,
  ...over,
});

// ---- indicators ----------------------------------------------------------------------

describe("range and change", () => {
  it("positions a value in its range and clamps outside values", () => {
    expect(rangePosition(1, { low: 0, high: 4 })).toBe(0.25);
    expect(rangePosition(9, { low: 0, high: 4 })).toBe(1);
    expect(rangePosition(-9, { low: 0, high: 4 })).toBe(0);
  });
  it("has no position for a missing value, a missing range or a flat range", () => {
    expect(rangePosition(null, { low: 0, high: 4 })).toBeNull();
    expect(rangePosition(1, null)).toBeNull();
    expect(rangePosition(1, { low: 2, high: 2 })).toBeNull();
  });
  it("flags a value outside the stored range", () => {
    expect(outsideRange(9, { low: 0, high: 4 })).toBe(true);
    expect(outsideRange(4, { low: 0, high: 4 })).toBe(false);
    expect(outsideRange(null, { low: 0, high: 4 })).toBe(false);
  });
  it("states a change only when both periods exist", () => {
    expect(pointChange(point())).toBeCloseTo(0.8, 12);
    expect(pointChange(point({ previousValue: null }))).toBeNull();
    expect(pointChange(point({ value: null }))).toBeNull();
    expect(pointChange(point({ value: 0, previousValue: 0 }))).toBe(0);
  });
});

describe("groupByGeography", () => {
  it("groups, names and orders by group size", () => {
    const g = groupByGeography([point({ geography: "US", seriesId: "b" }), point({ geography: "DE", seriesId: "z" }), point({ geography: "DE", seriesId: "a" })]);
    expect(g.map((x) => x.geography)).toEqual(["DE", "US"]);
    expect(g[0].name).toBe("Germany");
    expect(g[0].points.map((p) => p.seriesId)).toEqual(["a", "z"]);
  });
  it("keeps an unrecognised code as given", () => {
    expect(groupByGeography([point({ geography: "ZZ9" })])[0].name).toBe("ZZ9");
  });
});

// ---- comparison ----------------------------------------------------------------------

function comp(geography: string, value: number | null, period = "2025-12-31", over: Partial<ComparisonPoint> = {}): ComparisonPoint {
  return { seriesId: "cpi", source: "oecd", geography, unit: "percent", period, value, releaseAt: null, knownAt: "2026-01-01T00:00:00Z", sourceUrl: "u", ...over };
}

describe("comparisonGroups", () => {
  it("ranks high to low, ties by code, and lists countries without a value apart (never zero)", () => {
    const [g] = comparisonGroups([comp("DE", 2), comp("CZ", 2), comp("US", 3.1), comp("FR", null)]);
    expect(g.rows.map((r) => r.geography)).toEqual(["US", "CZ", "DE"]);
    expect(g.missing).toEqual(["FR"]);
  });
  it("flags differing latest periods", () => {
    const [g] = comparisonGroups([comp("DE", 2, "2025-12-31"), comp("US", 3, "2026-03-31")]);
    expect(g.mixedPeriods).toBe(true);
    expect(g.periodMin).toBe("2025-12-31");
    expect(g.periodMax).toBe("2026-03-31");
  });
  it("never mixes units or sources in one group", () => {
    const groups = comparisonGroups([comp("DE", 2), comp("US", 3, "2025-12-31", { unit: "index" }), comp("FR", 1, "2025-12-31", { source: "ecb" })]);
    expect(groups).toHaveLength(3);
  });
});

// ---- mapped targets ------------------------------------------------------------------

function effect(over: Partial<EffectRow>): EffectRow {
  return { ticker: "NVDA", issuerId: 7, segment: "s", basis: "reported", weight: 0.3, geography: "US", hsCode: null, unknownWeight: 0.2, explanation: "", dimension: "geography", relatedSeries: [], ...over };
}

describe("mappedTargets", () => {
  it("adds weights per target, keeps bases apart and skips zero or missing weights", () => {
    const t = mappedTargets([effect({ weight: 0.3 }), effect({ weight: 0.1, basis: "estimated", segment: "t" }), effect({ geography: "DE", weight: 0 }), effect({ geography: "FR", weight: null }), effect({ geography: null, hsCode: "8486", weight: 0.5 })]);
    expect(t.map((x) => `${x.kind}:${x.key}`)).toEqual(["hs_product:8486", "geography:US"]);
    const us = t.find((x) => x.key === "US")!;
    expect(us.weight).toBeCloseTo(0.4, 12);
    expect(us.bases).toEqual(["reported", "estimated"]);
  });
  it("keeps a country and a product with the same text apart", () => {
    expect(mappedTargets([effect({ geography: "US" }), effect({ geography: null, hsCode: "US" })])).toHaveLength(2);
  });
});

// ---- country exposure view -----------------------------------------------------------

describe("countryExposureView", () => {
  it("uses the backend ratio under 'all' and keeps the unmapped remainder explicit", () => {
    const v = countryExposureView(ctx([row("US", { reported: 300, estimated: 100 }), row("DE", { user_assigned: 100 })]), "all");
    expect(v.countries.map((c) => [c.code, c.value])).toEqual([["US", 0.4], ["DE", 0.1]]);
    expect(v.unmappedShare).toBeCloseTo(0.1, 12);
    expect(v.mappedShare).toBeCloseTo(0.5, 12);
  });
  it("filters by basis: a country with only other bases is a real zero, not missing", () => {
    const v = countryExposureView(ctx([row("US", { reported: 300, estimated: 100 }), row("DE", { user_assigned: 100 })]), "reported");
    expect(v.countries.find((c) => c.code === "US")!.value).toBeCloseTo(0.3, 12);
    expect(v.countries.find((c) => c.code === "DE")!.value).toBe(0);
  });
  it("has no share when the denominator is missing (null, not zero)", () => {
    const r = row("US", { reported: 300 });
    r.shareOfPortfolio = share(300, null);
    const v = countryExposureView(ctx([r]), "reported");
    expect(v.countries[0].value).toBeNull();
  });
  it("merges spellings of one country and separates the EU aggregate and unknown codes", () => {
    const v = countryExposureView(ctx([row("DE", { reported: 100 }), row("DEU", { estimated: 50 }), row("EU", { reported: 70 }), row("QQ", { reported: 10 })]), "all");
    expect(v.countries).toHaveLength(1);
    expect(v.countries[0].code).toBe("DE");
    expect(v.countries[0].value).toBeCloseTo(0.15, 12);
    expect(v.countries[0].byBasis).toEqual({ reported: 100, estimated: 50 });
    expect(v.aggregates.map((a) => a.code)).toEqual(["EU"]);
    expect(v.unrecognised.map((r) => r.key)).toEqual(["QQ"]);
  });
  it("is empty without a country concentration", () => {
    const v = countryExposureView(ctx([], { country: null }), "all");
    expect(v.countries).toEqual([]);
    expect(v.unmappedShare).toBeNull();
  });
  it("derives the remainder from the mapped ratio when the unmapped value is missing", () => {
    const base = ctx([row("US", { reported: 600 })]);
    base.country!.unmappedValue = null;
    expect(countryExposureView(base, "all").unmappedShare).toBeCloseTo(0.4, 12);
  });
});

// ---- exposure mappings ---------------------------------------------------------------

describe("summariseMappings", () => {
  it("sums active weights per issuer and dimension and keeps the remainder unknown", () => {
    const s = summariseMappings([mapping({ weight: 0.4 }), mapping({ id: 2, geography: "DE", weight: 0.3, basis: "estimated" }), mapping({ id: 3, geography: null, hsCode: "8486", hsRevision: "HS2022", weight: 0.9 })], "2026-10-03");
    expect(s).toHaveLength(1);
    expect(s[0].geography.mapped).toBeCloseTo(0.7, 12);
    expect(s[0].geography.unknown).toBeCloseTo(0.3, 12);
    expect(s[0].geography.byBasis).toEqual({ reported: 0.4, estimated: 0.3 });
    expect(s[0].product.mapped).toBeCloseTo(0.9, 12);
  });
  it("leaves expired and future mappings out of the totals", () => {
    const s = summariseMappings([mapping({ validTo: "2025-12-31" }), mapping({ id: 2, validFrom: "2027-01-01" }), mapping({ id: 3, weight: 0.2 })], "2026-10-03");
    expect(s[0].geography.mapped).toBeCloseTo(0.2, 12);
    expect(s[0].inactive).toBe(2);
  });
  it("flags over-allocation instead of scaling it away, and never shows a negative remainder", () => {
    const s = summariseMappings([mapping({ weight: 0.8 }), mapping({ id: 2, geography: "DE", weight: 0.5 })], "2026-10-03");
    expect(s[0].geography.overAllocated).toBe(true);
    expect(s[0].geography.unknown).toBe(0);
  });
  it("treats an issuer with nothing mapped as 100% unknown", () => {
    const s = summariseMappings([mapping({ validTo: "2020-01-01" })], "2026-10-03");
    expect(s[0].geography.unknown).toBe(1);
  });
  it("ignores missing weights", () => {
    expect(summariseMappings([mapping({ weight: null })], "2026-10-03")[0].geography.mapped).toBe(0);
  });
});

describe("isActiveMapping", () => {
  it("includes both ends of the validity window", () => {
    expect(isActiveMapping({ validFrom: "2026-10-03", validTo: "2026-10-03" }, "2026-10-03")).toBe(true);
    expect(isActiveMapping({ validFrom: "2026-10-04", validTo: null }, "2026-10-03")).toBe(false);
  });
});

describe("validateExposureForm", () => {
  const ok: ExposureForm = { issuerId: "7", segment: " Data centre ", geography: "us", hsCode: "", hsRevision: "", basis: "estimated", weightPercent: "12.5", validFrom: "2026-01-01", validTo: "" };
  it("builds the request body, converting percent to a clean fraction", () => {
    const { errors, body } = validateExposureForm(ok);
    expect(errors).toEqual({});
    expect(body).toEqual({ issuer_id: 7, segment: "Data centre", geography: "US", hs_code: null, hs_revision: null, basis: "estimated", weight: 0.125, valid_from: "2026-01-01", valid_to: null });
  });
  it("requires a geography or an HS code", () => {
    expect(validateExposureForm({ ...ok, geography: "" }).errors.form).toBeTruthy();
  });
  it("requires the HS revision with an HS code, and rejects a revision without one", () => {
    expect(validateExposureForm({ ...ok, geography: "", hsCode: "8486" }).errors.hsRevision).toBeTruthy();
    expect(validateExposureForm({ ...ok, geography: "", hsCode: "8486", hsRevision: "HS2022" }).body?.hs_revision).toBe("HS2022");
    expect(validateExposureForm({ ...ok, hsRevision: "HS2022" }).errors.hsRevision).toBeTruthy();
  });
  it("rejects both dimensions in one row", () => {
    expect(validateExposureForm({ ...ok, hsCode: "8486", hsRevision: "HS2022" }).errors.form).toBeTruthy();
  });
  it("validates every field", () => {
    const bad = validateExposureForm({ issuerId: "0", segment: "", geography: "D", hsCode: "", hsRevision: "", basis: "", weightPercent: "101", validFrom: "", validTo: "" });
    expect(Object.keys(bad.errors).sort()).toEqual(["basis", "geography", "issuerId", "segment", "validFrom", "weightPercent"]);
    expect(bad.body).toBeNull();
  });
  it("accepts the boundary weights 0 and 100 but not a blank or non-numeric one", () => {
    expect(validateExposureForm({ ...ok, weightPercent: "0" }).body?.weight).toBe(0);
    expect(validateExposureForm({ ...ok, weightPercent: "100" }).body?.weight).toBe(1);
    expect(validateExposureForm({ ...ok, weightPercent: "" }).errors.weightPercent).toBeTruthy();
    expect(validateExposureForm({ ...ok, weightPercent: "abc" }).errors.weightPercent).toBeTruthy();
  });
  it("rejects an end date before the start", () => {
    expect(validateExposureForm({ ...ok, validTo: "2025-01-01" }).errors.validTo).toBeTruthy();
  });
});

describe("apiErrorMessages", () => {
  it("reads a string detail (the 100% cap message)", () => {
    expect(apiErrorMessages({ detail: "exposure weights above 100%" })).toEqual(["exposure weights above 100%"]);
  });
  it("reads FastAPI validation errors with their field", () => {
    expect(apiErrorMessages({ detail: [{ loc: ["body", "weight"], msg: "Input should be less than or equal to 1" }] })).toEqual(["weight: Input should be less than or equal to 1"]);
  });
  it("returns nothing for an unknown body", () => {
    expect(apiErrorMessages(undefined)).toEqual([]);
    expect(apiErrorMessages({})).toEqual([]);
  });
});

// ---- trade ---------------------------------------------------------------------------

function claim(over: Partial<Claim>): Claim {
  return { claimId: "trade:842:276:export:HS2022:8486:2025-12-31", domain: "trade", subject: "US", metric: "trade_export", label: "l", value: 5_000_000_000, textValue: null, unit: "USD", currency: null, period: "2025-12-31", knownAsOf: "2026-02-01", source: "un_comtrade", provenance: "primary", sourceUrl: null, note: null, ...over };
}

describe("parseTradeClaim", () => {
  it("reads the natural key from the claim id", () => {
    const r = parseTradeClaim(claim({}))!;
    expect([r.reporter, r.partner, r.flow, r.hsRevision, r.hsCode, r.period]).toEqual(["842", "276", "export", "HS2022", "8486", "2025-12-31"]);
    expect(r.valueUsd).toBe(5_000_000_000);
  });
  it("rejects ids that do not have seven parts or a date, and non-trade claims", () => {
    expect(parseTradeClaim(claim({ claimId: "trade:842:276:export:8486:2025-12-31" }))).toBeNull();
    expect(parseTradeClaim(claim({ claimId: "trade:842:276:export:HS2022:8486:soon" }))).toBeNull();
    expect(parseTradeClaim(claim({ domain: "macro" }))).toBeNull();
  });
  it("does not call a non-USD value dollars", () => {
    expect(parseTradeClaim(claim({ unit: "CZK" }))!.valueUsd).toBeNull();
    expect(parseTradeClaim(claim({ value: null }))!.valueUsd).toBeNull();
  });
});

function research(claims: Claim[]): ResearchContext {
  return { scope: "country", subject: "US", asOf: "2026-10-03T00:00:00Z", claims, gaps: [], method: "m" };
}

describe("tradeRows / productSankey", () => {
  const claims = [
    claim({}),
    claim({ claimId: "trade:842:156:export:HS2022:8486:2025-12-31", value: 3e9 }),
    claim({ claimId: "trade:842:0:export:HS2022:8486:2025-12-31", value: 9e9 }),
    claim({ claimId: "trade:842:276:import:HS2022:8542:2025-12-31", value: 1e9 }),
    claim({ claimId: "broken" }),
    claim({ claimId: "trade:842:699:export:HS2022:8486:2025-12-31", value: null }),
  ];
  it("separates placeable rows from unplaceable trade claims", () => {
    const { rows, unparsed } = tradeRows(research(claims));
    expect(rows).toHaveLength(5);
    expect(unparsed).toHaveLength(1);
  });
  it("draws product-to-partner links of one flow, leaving World out and listing it apart", () => {
    const s = productSankey(tradeRows(research(claims)).rows, "export");
    expect(s.links.map((l) => l.value).sort()).toEqual([3e9, 5e9]);
    expect(s.drawn).toBe(2);
    expect(s.worldRows).toHaveLength(1);
    expect(s.nodes.filter((n) => n.column === 0)).toHaveLength(1);
    expect(s.nodes.map((n) => n.label)).toEqual(expect.arrayContaining(["HS 8486 (HS2022)", "Germany", "China"]));
    expect(s.period).toBe("2025-12-31");
  });
  it("does not draw a row without a USD value (India here) and keeps imports apart", () => {
    const s = productSankey(tradeRows(research(claims)).rows, "export");
    expect(s.nodes.some((n) => n.label === "India")).toBe(false);
    expect(productSankey(tradeRows(research(claims)).rows, "import").links).toHaveLength(1);
  });
  it("adds rows of the same product and partner within one revision only", () => {
    const rows = tradeRows(research([claim({ value: 1e9 }), claim({ claimId: "trade:840:276:export:HS2022:8486:2025-09-30", value: 2e9 }), claim({ claimId: "trade:842:276:export:HS2017:8486:2025-12-31", value: 4e9 })])).rows;
    const s = productSankey(rows, "export");
    expect(s.links).toHaveLength(2);
    expect(s.links.find((l) => l.source === "hs:8486:HS2022")!.value).toBe(3e9);
  });
  it("is empty for a flow with no rows", () => {
    const s = productSankey([], "export");
    expect(s.links).toEqual([]);
    expect(s.period).toBeNull();
  });
});

describe("trade helpers", () => {
  it("names M49 reporters through the Comtrade aliases and World", () => {
    expect(tradePartyName("842")).toBe("United States");
    expect(tradePartyName("0")).toBe("World");
    expect(tradePartyName("9999")).toBe("Code 9999");
  });
  it("expresses the Herfindahl index in points and equal partners, and not for a missing one", () => {
    expect(hhiPoints(0.25)).toBe(2500);
    expect(equalPartners(0.25)).toBe(4);
    expect(hhiPoints(null)).toBeNull();
    expect(equalPartners(0)).toBeNull();
  });
});
