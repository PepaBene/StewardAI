import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ChoroplethMap } from "@/components/charts/choropleth-map";
import { worldShapes } from "@/lib/charts/geo-world";
import type { ResourceState } from "@/lib/hooks/use-api-resource";
import type { Claim, HoldingMacroContext, IndicatorPoint, PortfolioCountryContext, TradeContext, TradeFlowPartners } from "@/lib/macro/types";

import { ClaimList } from "./context-cards";
import { formatShare } from "./format";
import { ExposureEditor } from "./exposure-editor";
import { ExposureTag } from "./exposure-tag";
import { HoldingMacroPanel } from "./holding-macro-panel";
import { IndicatorCard } from "./indicator-card";
import { PortfolioExposureMap } from "./portfolio-exposure-map";
import { ResourceBoundary } from "./resource-boundary";
import { ConcentrationTrend, PartnerSankey, ProductRows, TradeMetrics, TradeSummary } from "./trade-panel";

/*
 * Render smoke tests: every state of the macro views renders without NaN/undefined leaking
 * into the markup. Inputs are built inline.
 */

const clean = (html: string) => {
  expect(html).not.toMatch(/NaN|undefined|\[object/);
  return html;
};

function point(over: Partial<IndicatorPoint> = {}): IndicatorPoint {
  return { seriesId: "cpi", source: "oecd", geography: "DE", unit: "percent", period: "2026-08-31", value: 2.1, releaseAt: "2026-09-12T08:00:00Z", knownAt: "2026-09-12T09:00:00Z", sourceUrl: "https://x.test", previousPeriod: "2026-07-31", previousValue: 2.4, fiveYear: { low: -0.5, high: 9 }, tenYear: { low: -1, high: 10 }, state: "verified", stateReason: null, history: [], releaseCalendar: null, nextRelease: { kind: "unknown" }, ...over };
}

function context(over: Partial<HoldingMacroContext> = {}): HoldingMacroContext {
  return {
    asOf: "2026-10-03T10:00:00Z",
    observations: [point(), point({ seriesId: "rate", geography: "US", value: null, previousValue: null, fiveYear: null, tenYear: null, state: "quota_exhausted" })],
    coverage: [{ provider: "oecd", dataset: "cpi", geography: "DE", state: "partial", rawState: "vintage_unavailable", reason: "r", checkedAt: "2026-10-01T00:00:00Z" }],
    effects: [{ ticker: "SAP", issuerId: 3, segment: "Cloud", basis: "estimated", weight: 0.4, geography: "DE", hsCode: null, unknownWeight: 0.6, explanation: "e", dimension: "geography", relatedSeries: ["cpi"] }],
    comparisons: [
      { seriesId: "cpi", source: "oecd", geography: "DE", unit: "percent", period: "2026-08-31", value: 2.1, releaseAt: null, knownAt: "2026-09-12T09:00:00Z", sourceUrl: "u" },
      { seriesId: "cpi", source: "oecd", geography: "US", unit: "percent", period: "2026-07-31", value: 3.1, releaseAt: null, knownAt: "2026-09-12T09:00:00Z", sourceUrl: "u" },
    ],
    exposureUnknown: false,
    seriesEffects: [{ seriesId: "cpi", source: "oecd", unit: "percent", weightedChange: -0.12, coveredWeight: 0.4, geographies: ["DE"] }],
    ...over,
  };
}

const share = (n: number | null, d: number | null) => ({ numerator: n, denominator: d, ratio: n !== null && d ? n / d : null, denominatorBasis: "total portfolio value" });

function portfolio(over: Partial<PortfolioCountryContext> = {}): PortfolioCountryContext {
  return {
    snapshotDate: "2026-10-01",
    knownAsOf: "2026-10-02T00:00:00Z",
    baseCurrency: "CZK",
    totalValue: 1000,
    positionCount: 4,
    country: {
      mapped: share(600, 1000),
      unmappedValue: 400,
      unmappedTickers: ["AAA", "BBB"],
      rows: [
        { key: "US", exposedValue: 400, shareOfPortfolio: share(400, 1000), shareOfMapped: null, byBasis: { reported: 300, estimated: 100 }, holdings: 2 },
        { key: "DE", exposedValue: 100, shareOfPortfolio: share(100, 1000), shareOfMapped: null, byBasis: { user_assigned: 100 }, holdings: 1 },
        { key: "EU", exposedValue: 100, shareOfPortfolio: share(100, 1000), shareOfMapped: null, byBasis: { estimated: 100 }, holdings: 1 },
      ],
      hhi: 0.5,
      hhiDenominator: 600,
      note: "n",
    },
    product: null,
    gaps: [],
    ...over,
  };
}

describe("IndicatorCard", () => {
  it("renders value, previous period, range and provenance", () => {
    const html = clean(renderToStaticMarkup(createElement(IndicatorCard, { point: point() })));
    expect(html).toContain("cpi");
    expect(html).toContain("Germany");
    expect(html).toContain("10-year range");
    expect(html).toContain("−0.30 percent");
    expect(html).toContain("Known since");
  });
  it("shows a missing value as n/a, never as 0, and states the lack of a range", () => {
    const html = clean(renderToStaticMarkup(createElement(IndicatorCard, { point: point({ value: null, previousValue: null, fiveYear: null, tenYear: null, releaseAt: null, state: "quota_exhausted" }) })));
    expect(html).toContain("n/a");
    expect(html).toContain("No 5- or 10-year range");
    expect(html).toContain("Quota exhausted");
    expect(html).toContain("release date not recorded");
  });
});

describe("HoldingMacroPanel", () => {
  it("renders mapped exposure, indicators, series effects, comparison and coverage", () => {
    const html = clean(renderToStaticMarkup(createElement(HoldingMacroPanel, { ctx: context(), ticker: "SAP" })));
    expect(html).toContain("Mapped exposure of SAP");
    expect(html).toContain("Estimated");
    expect(html).toContain("The same indicators across countries");
    expect(html).toContain("Latest periods differ");
    expect(html).toContain("Collection state for the mapped countries");
    expect(html).toContain("Latest vintage");
    expect(html).toContain("First release");
  });
  it("says exposure is unknown, and shows no indicator, when nothing is mapped", () => {
    const html = clean(renderToStaticMarkup(createElement(HoldingMacroPanel, { ctx: context({ exposureUnknown: true, effects: [], observations: [], seriesEffects: [], comparisons: [], coverage: [] }), ticker: "SAP" })));
    expect(html).toContain("Exposure unknown");
    expect(html).not.toContain("Latest values for the mapped countries");
  });
  it("names mapped countries that have no stored series", () => {
    const html = clean(renderToStaticMarkup(createElement(HoldingMacroPanel, { ctx: context({ observations: [] }), ticker: "SAP" })));
    expect(html).toContain("Not covered");
  });
});

describe("PortfolioExposureMap", () => {
  it("renders the frame, table equivalent, explicit unknown remainder and basis labels", () => {
    const html = clean(renderToStaticMarkup(createElement(PortfolioExposureMap, { ctx: portfolio(), coverage: null })));
    expect(html).toContain("Where the portfolio is exposed");
    expect(html).toContain("Unknown (unmapped)");
    expect(html).toContain("Owner-assigned");
    expect(html).toContain("Table view");
    expect(html).toContain("60.0% of portfolio value is mapped");
  });
  it("renders the empty state when no country is mapped", () => {
    const html = clean(renderToStaticMarkup(createElement(PortfolioExposureMap, { ctx: portfolio({ country: null }), coverage: null })));
    expect(html).toContain("No portfolio value has an active country mapping");
  });
});

describe("ChoroplethMap", () => {
  const base = { formatValue: (v: number) => `${(v * 100).toFixed(1)}%`, unitLabel: "% of portfolio", ariaLabel: "Map" };
  it("draws countries, keeps a zero apart from missing and makes only countries with data focusable", () => {
    const html = clean(renderToStaticMarkup(createElement(ChoroplethMap, { ...base, data: [{ code: "US", value: 0.4 }, { code: "DE", value: 0 }, { code: "FR", value: null }, { code: "EU", value: 0.1 }] })));
    expect(html).toContain("<pattern");
    expect(html).toContain("fill-accent-ramp-");
    expect(html).toContain("fill-surface-raised");
    expect(html).toContain("United States of America: 40.0%");
    expect(html).toContain("Germany: Zero");
    expect(html).toContain("France: No data / not covered");
    expect(html).toContain("not drawn on the map");
    expect(html.match(/tabindex="0"/g)?.length).toBe(2); // US and DE only; FR has no value
  });
  it("renders extra ranked rows, including the dashed unknown remainder", () => {
    const html = clean(renderToStaticMarkup(createElement(ChoroplethMap, { ...base, data: [{ code: "US", value: 0.4 }], extraRows: [{ key: "u", label: "Unknown", value: 0.6, kind: "unknown", note: "n" }] })));
    expect(html).toContain("Unknown");
    expect(html).toContain("border-dashed");
  });
  it("renders an empty state with no data", () => {
    expect(clean(renderToStaticMarkup(createElement(ChoroplethMap, { ...base, data: [] })))).toContain("No country values to map");
  });
  it("renders when every value is zero (no ramp classes)", () => {
    const html = clean(renderToStaticMarkup(createElement(ChoroplethMap, { ...base, data: [{ code: "US", value: 0 }] })));
    expect(html).not.toContain("fill-accent-ramp-");
  });
});

describe("ExposureEditor", () => {
  it("renders with no mappings and says exposure is unknown", () => {
    const html = clean(renderToStaticMarkup(createElement(ExposureEditor, { mappings: [], issuers: new Map(), defaultIssuerId: null, onChanged: () => {} })));
    expect(html).toContain("No exposure mapping exists yet");
    expect(html).toContain("Add a mapping");
  });
  it("renders mappings with unknown remainder and a delete control", () => {
    const html = clean(
      renderToStaticMarkup(
        createElement(ExposureEditor, {
          mappings: [{ id: 1, issuerId: 3, segment: "Cloud", geography: "DE", hsRevision: null, hsCode: null, basis: "user_assigned", weight: 0.4, validFrom: "2020-01-01", validTo: null }],
          issuers: new Map([[3, "SAP"]]),
          defaultIssuerId: 3,
          onChanged: () => {},
        }),
      ),
    );
    expect(html).toContain("SAP (issuer 3)");
    expect(html).toContain("60.0% unknown");
    expect(html).toContain("Delete");
    expect(html).toContain("Owner-assigned");
  });
});

const period = (over: Record<string, unknown> = {}) => ({ period: "2025-12-31", total: 10e9, totalBasis: "world_row" as const, mappedValue: 6e9, unmappedValue: 4e9, unmappedShare: 0.4, partnerCount: 2, hhi: 0.25, partnersExceedTotal: false, ...over });
const flowOf = (over: Partial<TradeFlowPartners> = {}): TradeFlowPartners => ({
  flow: "export",
  period: "2025-12-31",
  summary: period(),
  partners: [
    { partner: "276", partnerIso: "DE", value: 4e9, share: 0.4 },
    { partner: "899", partnerIso: null, value: 2e9, share: 0.2 },
  ],
  history: [period({ period: "2024-12-31", hhi: 0.3 }), period()],
  ...over,
});
const tradeOf = (over: Partial<TradeContext> = {}): TradeContext => ({ reporter: "US", hsCode: "8486", hsRevision: "HS2022", partner: null, periods: 3, latestPeriod: "2025-12-31", latestByFlow: { export: 10e9 }, currency: "USD", metrics: { export_growth: 0.12, import_growth: null, export_partner_concentration: 0.25, product_mix_change: null }, sourceUrl: null, knownAt: null, notice: "n", reporterWide: null, partners: [flowOf()], ...over });

describe("trade views", () => {
  it("renders the partner Sankey from partner rows with the unmapped remainder as its own target", () => {
    const html = clean(renderToStaticMarkup(createElement(PartnerSankey, { trade: tradeOf() })));
    expect(html).toContain("Germany");
    expect(html).toContain("code 899");
    expect(html).toContain("Unmapped remainder");
    expect(html).toContain("40.0%");
  });
  it("says the remainder is not stated (no World total), never zero, when it is null", () => {
    const html = clean(renderToStaticMarkup(createElement(PartnerSankey, { trade: tradeOf({ partners: [flowOf({ summary: period({ unmappedValue: null, unmappedShare: null }) })] }) })));
    expect(html).toContain("not stated");
    expect(html).toContain("no World total");
    expect(html).not.toContain("Unmapped remainder</text>");
  });
  it("renders a not-covered placeholder with no partner rows", () => {
    const html = clean(renderToStaticMarkup(createElement(PartnerSankey, { trade: tradeOf({ partners: [] }) })));
    expect(html).toContain("Not covered");
  });
  it("draws the concentration trend on the 10,000 axis and shows the partners-exceed-total flag", () => {
    const f = flowOf({ history: [period({ period: "2024-12-31", hhi: 0.3, partnersExceedTotal: true }), period()] });
    const html = clean(renderToStaticMarkup(createElement(ConcentrationTrend, { flow: f })));
    expect(html).toContain("of 10,000");
    expect(html).toContain("Partners exceed the total in Dec 31, 2024");
    expect(clean(renderToStaticMarkup(createElement(ConcentrationTrend, { flow: flowOf() })))).not.toContain("Partners exceed the total");
  });
  it("renders trade metrics with missing ones as not available", () => {
    const html = clean(renderToStaticMarkup(createElement(TradeMetrics, { metrics: { export_growth: 0.12, import_growth: null, export_partner_concentration: 0.25, revealed_trade_dependence: null } })));
    expect(html).toContain("+12.0%");
    expect(html).toContain("2,500");
    expect(html).toContain("not available");
  });
  it("keeps the claim-based product rows list working for the industry context", () => {
    const c: Claim = { claimId: "trade:842:276:export:HS2022:8486:2025-12-31", domain: "trade", subject: "8486", metric: "m", label: "l", value: 5e9, textValue: null, unit: "USD", currency: null, period: "2025-12-31", knownAsOf: "2026-02-01", source: "un_comtrade", provenance: "primary", sourceUrl: null, note: null };
    const html = clean(renderToStaticMarkup(createElement(ProductRows, { context: { scope: "industry", subject: "8486", asOf: "2026-10-03T00:00:00Z", claims: [c], gaps: [], method: "m" }, hsCode: "8486" })));
    expect(html).toContain("Germany");
  });
});

describe("ClaimList / ExposureTag", () => {
  it("renders cited claims and tolerates a missing value", () => {
    const c: Claim = { claimId: "macro:x", domain: "macro", subject: "DE", metric: "m", label: "cpi (DE)", value: null, textValue: null, unit: "percent", currency: null, period: null, knownAsOf: "2026-09-12", source: "oecd", provenance: "primary", sourceUrl: "https://x.test", note: "Previous 2.0" };
    const html = clean(renderToStaticMarkup(createElement(ClaimList, { claims: [c] })));
    expect(html).toContain("n/a");
    expect(html).toContain("Previous 2.0");
  });
  it("tags the three bases with distinct borders", () => {
    const h = (b: "reported" | "estimated" | "user_assigned") => renderToStaticMarkup(createElement(ExposureTag, { basis: b }));
    expect(new Set([h("reported"), h("estimated"), h("user_assigned")]).size).toBe(3);
  });
});

describe("ResourceBoundary", () => {
  // eslint-disable-next-line react/no-children-prop -- ResourceBoundary takes a render function as its children
  const render = <T,>(state: ResourceState<T>) => clean(renderToStaticMarkup(createElement(ResourceBoundary<T>, { state, what: "things", children: (d: T) => createElement("p", null, `data:${String(d)}`) })));
  it("renders loading, ok, 404 and error states", () => {
    expect(render({ kind: "loading" })).toContain("Loading things");
    expect(render({ kind: "ok", data: "x" })).toContain("data:x");
    expect(render({ kind: "error", title: "API responded with HTTP 404", detail: "No snapshot", previous: null, status: 404 })).toContain("Not covered");
    const err = render({ kind: "error", title: "Backend unreachable", detail: "down", previous: null });
    expect(err).toContain("Could not load things");
    expect(err).toContain("Temporarily unavailable");
  });
  it("keeps the last good data beside an error", () => {
    const html = render({ kind: "error", title: "Backend unreachable", detail: "down", previous: "old" });
    expect(html).toContain("data:old");
    expect(html).toContain("last data received is still shown");
  });
});

describe("fix pass: choropleth list, negatives, tooltip box", () => {
  const base = { formatValue: (v: number) => `${(v * 100).toFixed(1)}%`, unitLabel: "% of portfolio", ariaLabel: "Map" };
  it("lists a country with a value but no polygon once (as 'not drawn'), not twice", () => {
    expect(worldShapes().some((s) => s.code === "SG")).toBe(false);
    const html = clean(renderToStaticMarkup(createElement(ChoroplethMap, { ...base, data: [{ code: "US", value: 0.4 }, { code: "SG", value: 0.04 }] })));
    expect(html.match(/Singapore/g)).toHaveLength(1);
    expect(html.match(/not drawn on the map/g)).toHaveLength(1);
    expect(html.match(/4\.0%/g)).toHaveLength(1);
  });
  it("names a negative value as outside the colour scale instead of a bare number under a hatched fill", () => {
    const html = clean(renderToStaticMarkup(createElement(ChoroplethMap, { ...base, data: [{ code: "US", value: 0.4 }, { code: "FR", value: -0.02 }] })));
    expect(html).toContain("negative, not on the colour scale");
  });
  it("labels legend classes by what they contain (over lo, up to hi) and clips the map box", () => {
    const html = clean(renderToStaticMarkup(createElement(ChoroplethMap, { ...base, data: [{ code: "US", value: 0.4 }] })));
    expect(html).toMatch(/over \d+\.\d% up to \d+\.\d%/);
    expect(html).toContain("overflow-x-clip");
  });
});

describe("fix pass: share formatting at tiny values", () => {
  it("does not print a real share as 0.00% and keeps small legend edges distinct", () => {
    expect(formatShare(0.00004)).toBe("0.0040%");
    expect(formatShare(0.00025)).toBe("0.025%");
    expect(formatShare(0.05)).toBe("5.0%");
    expect(formatShare(0)).toBe("0.0%");
    expect(formatShare(1e-9)).toBe("<0.0001%");
  });
});

describe("fix pass: macro panels", () => {
  it("shows one Germany heading when indicators arrive as DE and DEU", () => {
    const html = clean(renderToStaticMarkup(createElement(HoldingMacroPanel, { ctx: context({ observations: [point({ geography: "DE" }), point({ geography: "DEU", seriesId: "rate" })] }), ticker: "SAP" })));
    expect(html.match(/aria-label="Germany indicators"/g)).toHaveLength(1);
  });
  it("says a mapping added now cannot appear at a past as-of date", () => {
    const empty = context({ exposureUnknown: true, effects: [], observations: [], seriesEffects: [], comparisons: [], coverage: [] });
    const past = clean(renderToStaticMarkup(createElement(HoldingMacroPanel, { ctx: empty, ticker: "SAP", pastAsOf: true })));
    expect(past).toContain("cannot appear at this past date");
    expect(past).not.toContain("Add a mapping below");
    expect(clean(renderToStaticMarkup(createElement(HoldingMacroPanel, { ctx: empty, ticker: "SAP" })))).toContain("Add a mapping below");
  });
  it("trade: product-scoped figures, with the reporter-wide context in a collapsed block", () => {
    const html = clean(renderToStaticMarkup(createElement(TradeSummary, { trade: tradeOf({ hsRevision: "mixed", periods: 12, reporterWide: { periods: 40, hsRevision: "mixed", products: 5, metrics: { product_mix_change: 0.2 } } }) })));
    expect(html).toContain("12 periods stored for it");
    expect(html).toContain("Reporter-wide context");
    expect(html).toContain("These figures are not specific to HS 8486");
    expect(html).toContain("Product mix change");
    expect(html).toContain("mixed (several revisions are stored)");
    expect(html).not.toContain("(mixed)");
    expect(html).not.toContain("not from HS 8486 alone");
    expect(html).not.toMatch(/<details[^>]*\sopen/);
  });
  it("trade: the product-scoped tiles omit the always-null product mix change", () => {
    const html = clean(renderToStaticMarkup(createElement(TradeSummary, { trade: tradeOf() })));
    expect(html).not.toContain("Product mix change");
  });
});

describe("fix pass: exposure editor issuer id", () => {
  const editor = (over: Record<string, unknown>) =>
    clean(renderToStaticMarkup(createElement(ExposureEditor, { mappings: [], issuers: new Map(), defaultIssuerId: null, onChanged: () => {}, ...over })));
  it("tells the owner when the holding's issuer id is unknown, instead of a bare number box", () => {
    const html = editor({ ticker: "SAP" });
    expect(html).toContain("SAP has no mapping yet");
    expect(html).toContain("the company name is shown before you save");
  });
  it("shows the prefilled id's holding, offers known issuers, and states the geography rule", () => {
    const html = editor({ ticker: "SAP", issuers: new Map([[3, "SAP"]]), defaultIssuerId: 3, mappings: [{ id: 1, issuerId: 3, segment: "Cloud", geography: "DE", hsRevision: null, hsCode: null, basis: "reported", weight: 0.4, validFrom: "2020-01-01", validTo: null }] });
    expect(html).toContain("Prefilled with SAP");
    expect(html).toContain("Issuer 3: SAP");
    expect(html).toContain("SAP (issuer 3)</option>");
    expect(html).toContain("GB (not UK)");
  });
});

describe("fix pass: source links are http(s) only", () => {
  it("renders no link for a javascript: or data: source URL on an indicator card or a claim", () => {
    const card = clean(renderToStaticMarkup(createElement(IndicatorCard, { point: point({ sourceUrl: "javascript:alert(1)" }) })));
    expect(card).not.toContain("javascript:");
    expect(card).toContain("Source link not available");
    const claim: Claim = { claimId: "c1", domain: "macro", subject: "DE", metric: "m", label: "l", value: 1, textValue: null, unit: "percent", currency: null, period: "2026-01-01", knownAsOf: "2026-02-01", source: "oecd", provenance: "primary", sourceUrl: "data:text/html,x", note: null };
    const list = clean(renderToStaticMarkup(createElement(ClaimList, { claims: [claim] })));
    expect(list).not.toContain("data:text/html");
    expect(card).toContain("<span>Source link not available</span>");
    expect(clean(renderToStaticMarkup(createElement(IndicatorCard, { point: point() })))).toContain('href="https://x.test"');
  });
});

describe("adoption: vintage small multiples", () => {
  const hist = [
    { period: "2026-06-30", valueLatest: 2.0, valueFirstRelease: null, vintageUnavailable: true, releasedAt: null, revisedAt: null, revisionCount: 0 },
    { period: "2026-07-31", valueLatest: 2.4, valueFirstRelease: 2.2, vintageUnavailable: false, releasedAt: "2026-08-12T08:00:00Z", revisedAt: "2026-09-12T08:00:00Z", revisionCount: 1 },
  ];
  const withHist = (over: Partial<IndicatorPoint> = {}) => point({ history: hist, releaseCalendar: "Monthly, mid-month", ...over });
  const dom = [Date.UTC(2020, 0, 1), Date.UTC(2026, 8, 1)] as const;
  it("draws the history with revision markers and the release-calendar text", () => {
    const html = clean(renderToStaticMarkup(createElement(IndicatorCard, { point: withHist(), mode: "latest", domain: dom })));
    expect(html).toContain('data-vintage-chart="latest"');
    expect(html).toContain("data-revision-marker");
    expect(html).toContain("Monthly, mid-month");
  });
  it("first release: an unarchived period is 'vintage unavailable', never the revised value", () => {
    const only = withHist({ history: [hist[0]] });
    const html = clean(renderToStaticMarkup(createElement(IndicatorCard, { point: only, mode: "first_release", domain: dom })));
    expect(html).toContain("Vintage unavailable");
    expect(html).toContain("not substituted");
    expect(html).not.toContain("stroke-chart-line");
  });
  it("states a missing release calendar as not stated", () => {
    expect(clean(renderToStaticMarkup(createElement(IndicatorCard, { point: point(), mode: "latest", domain: dom })))).toContain("not stated");
  });
  it("panel offers the Latest vintage / First release toggle and aligns charts on one axis", () => {
    const ctx = context({ observations: [withHist(), withHist({ seriesId: "rate", history: [{ ...hist[1], period: "2019-01-31" }] })] });
    const html = clean(renderToStaticMarkup(createElement(HoldingMacroPanel, { ctx, ticker: "SAP" })));
    expect(html).toContain("Latest vintage");
    expect(html).toContain("First release");
    expect(html.match(/>2019</g)?.length).toBe(2);
  });
  it("renders access_unverified through the shared state vocabulary", () => {
    expect(clean(renderToStaticMarkup(createElement(IndicatorCard, { point: point({ state: "access_unverified" }) })))).toContain("Access unverified");
  });
});

describe("third wave: next release display", () => {
  const cal = { kind: "calendar" as const, date: "2026-10-14", time: "08:30:00", source: { provider: "fred", url: "https://fred.stlouisfed.org/releases/calendar", title: "CPI" } };
  it("shows the provider date with time and a source link, as published", () => {
    const html = clean(renderToStaticMarkup(createElement(IndicatorCard, { point: point({ nextRelease: cal }) })));
    expect(html).toContain("Oct 14, 2026, 08:30");
    expect(html).toContain('href="https://fred.stlouisfed.org/releases/calendar"');
    expect(html).toContain("as published in fred release calendar");
    expect(html).not.toMatch(/estimate/);
  });
  it("labels an estimate plainly and never presents it as a published date", () => {
    const html = clean(renderToStaticMarkup(createElement(IndicatorCard, { point: point({ nextRelease: { kind: "estimate", date: "2026-10-20" } }) })));
    expect(html).toContain("about Oct 20, 2026 (estimate, based on observed cadence)");
    expect(html).toContain("not a published date");
    expect(html).not.toContain("as published");
  });
  it("shows unknown when neither exists", () => {
    const html = clean(renderToStaticMarkup(createElement(IndicatorCard, { point: point() })));
    expect(html).toContain('data-next-release="unknown"');
    expect(html).toContain("Next release unknown");
  });
  it("an unsafe calendar url is not linked", () => {
    const html = renderToStaticMarkup(createElement(IndicatorCard, { point: point({ nextRelease: { ...cal, source: { provider: "fred", url: "javascript:alert(1)", title: null } } }) }));
    expect(html).not.toContain("javascript:");
  });
  it("the panel has a table equivalent listing every series' next release and its basis", () => {
    const ctx = context({ observations: [point({ nextRelease: cal }), point({ seriesId: "rate", nextRelease: { kind: "estimate", date: "2026-10-20" } })] });
    const html = clean(renderToStaticMarkup(createElement(HoldingMacroPanel, { ctx, ticker: "SAP" })));
    expect(html).toContain("Next release dates (table view)");
    expect(html).toContain("Provider&#x27;s published calendar");
    expect(html).toContain("Estimate based on observed cadence, not published");
  });
});
