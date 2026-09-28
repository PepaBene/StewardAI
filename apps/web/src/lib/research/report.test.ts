import { describe, expect, it } from "vitest";

import type { FactOut, KpiStatus, ResearchBody, Source } from "@/lib/api/client";
import { chipsFor, primarySourceLink, sourceLinks, sourcePreview } from "@/lib/sources/sources";

import {
  claimTally,
  conclusionSection,
  DEFAULT_PROFILE_OPTIONS,
  factInKpiUnit,
  keyFigures,
  kpiFigure,
  normaliseTicker,
  profileOptions,
  RESEARCH_SECTIONS,
  researchCitations,
  researchIndex,
  sectionLabel,
  SECTION_IDS,
  stepLabel,
  triggerLabel,
  valuationLabel,
  writtenBy,
} from "./report";

const source = (id: string, over: Partial<Source> = {}): Source => ({
  id,
  kind: "fact",
  ref: id,
  title: `Source ${id}`,
  url: null,
  app_path: null,
  tier: null,
  snippet: null,
  as_of: null,
  ...over,
});

const BODY: ResearchBody = {
  ticker: "AAPL_US_EQ",
  name: "Apple",
  owned: true,
  headline: "Apple: steady services growth",
  profile_id: "standard",
  profile_name: "Standard workup",
  depth: "standard",
  model: "claude-opus-5-5",
  language: "en",
  sections: [
    {
      id: "financial_trend",
      title: "Financial trend",
      written_by: "code",
      sentences: [
        { text: "Revenue rose 6% to USD 94.9 bn.", source_ids: ["S1"], written_by: "code", check: null, check_reason: null },
        { text: "Net income was USD 23.6 bn.", source_ids: ["S2", "S1"], written_by: "code" },
      ],
      note: null,
    },
    {
      id: "business",
      title: "Business",
      written_by: "model",
      sentences: [
        { text: "Services now make a quarter of revenue.", source_ids: ["S3"], written_by: "model", check: "supported" },
        { text: "Wearables are declining.", source_ids: ["S4"], written_by: "model", check: "unsupported", check_reason: "Not in S4." },
      ],
    },
  ],
  valuation: [
    { method: "pe_ttm", value: 31.2, display: "31.2×", formula: "price / EPS TTM = 230.1 / 7.38", source_ids: ["S5", "S1"] },
  ],
  sources: [
    source("S1", { app_path: "/evidence/12", url: "https://www.sec.gov/x", snippet: "Revenue 94,930" }),
    source("S2", { app_path: "/evidence/13" }),
    source("S3", { kind: "news_item", url: "https://www.reuters.com/a", tier: 1, snippet: "Services revenue..." }),
    source("S4", { kind: "search", url: "https://www.ft.com/b", tier: 2 }),
    source("S5", { kind: "price", app_path: "/holdings/AAPL_US_EQ" }),
    source("S6", { kind: "strategy", app_path: "/config/strategy" }),
  ],
  claims: null,
  steps_used: 5,
  searches_used: 2,
  dropped_sentences: 1,
  notes: [],
};

describe("research report: citations and source cards", () => {
  it("resolves every cited id to a source card, and lists every source as a card", () => {
    const index = researchIndex(BODY);
    // Every source used appears in the sidebar, once, in the stored order.
    expect(index.cards.map((c) => c.source.id)).toEqual(BODY.sources.map((s) => s.id));
    expect(index.cards.map((c) => c.number)).toEqual([1, 2, 3, 4, 5, 6]);
    // Every claim-level citation in the body links to one of those cards.
    const cited = researchCitations(BODY).flat();
    expect(cited.length).toBeGreaterThan(0);
    for (const id of cited) {
      const [chip] = chipsFor([id], index);
      expect(chip.card, `citation ${id}`).not.toBeNull();
      expect(chip.href).toBe(`#${chip.card!.anchor}`);
      expect(index.cards).toContain(chip.card);
    }
    expect(index.unresolved).toEqual([]);
    expect(index.uncited).toEqual(["S6"]);
    expect(index.byId.get("S1")!.citedBy).toBe(3);
  });

  it("reports a citation with no card instead of dropping it", () => {
    const body = { ...BODY, sections: [{ ...BODY.sections[0], sentences: [{ text: "x", source_ids: ["S9"], written_by: "code" as const }] }] };
    const index = researchIndex(body);
    expect(index.unresolved).toEqual(["S9"]);
    expect(chipsFor(["S9"], index)).toEqual([{ id: "S9", label: "S9", href: null, card: null }]);
  });

  it("numbers chips in citation order, each source once", () => {
    const index = researchIndex(BODY);
    expect(chipsFor(["S2", "S1", "S2"], index).map((c) => c.label)).toEqual(["2", "1"]);
  });
});

describe("source links", () => {
  it("prefers the app page and keeps the external page as a second link", () => {
    expect(sourceLinks(BODY.sources[0])).toEqual({ internal: "/evidence/12", external: "https://www.sec.gov/x" });
    expect(primarySourceLink(BODY.sources[0])).toEqual({ href: "/evidence/12", external: false });
    expect(primarySourceLink(BODY.sources[2])).toEqual({ href: "https://www.reuters.com/a", external: true });
  });

  it("refuses protocol-relative paths and non-http urls", () => {
    expect(sourceLinks({ app_path: "//evil.test/x", url: "javascript:alert(1)" })).toEqual({ internal: null, external: null });
    expect(primarySourceLink({ app_path: null, url: null })).toBeNull();
  });

  it("previews the snippet and the tier", () => {
    const preview = sourcePreview(BODY.sources[2]);
    expect(preview.snippet).toBe("Services revenue...");
    expect(preview.tierLabel).toBe("Tier 1");
    expect(preview.source).toBe("News item · reuters.com");
    expect(sourcePreview(BODY.sources[1]).tierLabel).toMatch(/No tier/);
  });
});

describe("research labels and profiles", () => {
  it("labels sections, valuation methods and the writer", () => {
    expect(SECTION_IDS).toHaveLength(10);
    expect(RESEARCH_SECTIONS.filter((s) => s.writtenBy === "model").map((s) => s.id)).toEqual([
      "business",
      "moat_risks",
      "strategy_fit",
      "open_questions",
    ]);
    expect(sectionLabel("moat_risks")).toBe("Moat and risks");
    expect(valuationLabel("pe_ttm")).toBe("P/E (TTM)");
    expect(writtenBy("model")).toBe("model");
    expect(writtenBy(undefined)).toBe("code");
  });

  it("offers the saved profiles, or the backend defaults when none is saved", () => {
    expect(profileOptions(null)).toEqual({ options: DEFAULT_PROFILE_OPTIONS, defaultId: "standard" });
    expect(
      profileOptions({
        default_profile: "deep",
        profiles: [
          { id: "fast", name: "Fast", sections: ["business"] } as never,
          { id: "deep", name: "Deep", sections: ["business"] } as never,
        ],
      }),
    ).toEqual({ options: [{ id: "fast", name: "Fast" }, { id: "deep", name: "Deep" }], defaultId: "deep" });
  });

  it("checks tickers as the API does", () => {
    expect(normaliseTicker(" AAPL_US_EQ ")).toBe("AAPL_US_EQ");
    expect(normaliseTicker("A B")).toBeNull();
    expect(normaliseTicker("")).toBeNull();
  });
});

describe("research report page", () => {
  const fact = (id: number, fiscal_period: string, period_end: string, value: number, over: Partial<FactOut> = {}): FactOut =>
    ({
      id,
      metric: "china_system_sales_share",
      basis: "reported",
      status: "verified",
      value,
      value_units: value,
      value_as_written: String(value),
      scale: "units",
      unit: "percent",
      currency: null,
      period: `FY2026 ${fiscal_period}`,
      fiscal_year: 2026,
      fiscal_period,
      period_end,
      ...over,
    }) as FactOut;
  const kpiDef = (metric: string, unit = "%") =>
    ({ id: metric, metric, operator: "<=", threshold: 30, unit, basis: "reported", period: "quarter", description: "" }) as const;

  it("labels steps, finds a conclusion section and tallies the claim check", () => {
    expect(stepLabel("write_business")).toBe("Write business");
    expect(stepLabel("gather")).toBe("Gather");
    expect(conclusionSection([{ id: "business" }, { id: "summary" }])).toEqual({ id: "summary" });
    expect(conclusionSection([{ id: "business" }])).toBeNull();
    expect(claimTally({ supported: 13, unsupported: 0, unchecked: 2 })).toEqual({ supported: 13, total: 15 });
    expect(claimTally({ supported: 0, unsupported: 0, unchecked: 0 })).toBeNull();
    expect(claimTally(null)).toBeNull();
    expect(triggerLabel("manual")).toBe("By hand");
    expect(triggerLabel("earnings_check")).toBe("Earnings check");
  });

  it("lists each cited metric once with its stored history", () => {
    const facts = [
      fact(84, "Q2", "2026-06-30", 41),
      fact(70, "Q1", "2026-03-31", 38),
      fact(71, "Q1", "2026-03-31", 99, { status: "rejected" }),
      fact(83, "Q2", "2026-06-30", 5.54, { metric: "net_bookings", unit: "currency", scale: "billions", currency: "EUR", value_as_written: "5.54", value_units: 5.54e9 }),
    ];
    const sources = [
      { id: "S2", kind: "fact" as const, ref: "84" },
      { id: "S9", kind: "fact" as const, ref: "70" },
      { id: "S1", kind: "fact" as const, ref: "83" },
      { id: "S3", kind: "price" as const, ref: "asml.nl" },
      { id: "S4", kind: "fact" as const, ref: "999" },
    ];
    expect(keyFigures(sources, facts)).toEqual([
      { sourceId: "S2", factId: 84, label: "China system sales share · FY2026 Q2", value: "41%", series: [38, 41] },
      { sourceId: "S1", factId: 83, label: "Net bookings · FY2026 Q2", value: "EUR 5.54 bn", series: [5.54e9] },
    ]);
  });

  it("charts a cited KPI with history against its threshold, breaching first", () => {
    const facts = [
      fact(84, "Q2", "2026-06-30", 41),
      fact(70, "Q1", "2026-03-31", 38),
      fact(60, "Q2", "2026-06-30", 53.7, { metric: "gross_margin" }),
      fact(61, "Q1", "2026-03-31", 52, { metric: "gross_margin" }),
    ];
    const kpis = [
      { kpi: kpiDef("gross_margin"), current: null },
      { kpi: kpiDef("china_system_sales_share"), current: { status: "breach" } as KpiStatus["current"] },
    ];
    const fig = kpiFigure(kpis, facts, [84, 60]);
    expect(fig?.kpi.metric).toBe("china_system_sales_share");
    expect(fig?.status).toBe("breach");
    expect(fig?.points).toEqual([
      { label: "Q1·26", value: 38 },
      { label: "Q2·26", value: 41 },
    ]);
    expect(kpiFigure(kpis, facts, [])).toBeNull();
    expect(kpiFigure(kpis, [fact(84, "Q2", "2026-06-30", 41)], [84])).toBeNull();
    expect(factInKpiUnit({ value: 5.54, value_units: 5.54e9, unit: "currency" }, "EUR bn")).toBeCloseTo(5.54);
    expect(factInKpiUnit({ value: 180, value_units: 180e6, unit: "currency" }, "USD m")).toBeCloseTo(180);
  });
});
