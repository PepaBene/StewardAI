import { describe, expect, it } from "vitest";

import type { ResearchBody, Source } from "@/lib/api/client";
import { chipsFor, primarySourceLink, sourceLinks, sourcePreview } from "@/lib/sources/sources";

import {
  DEFAULT_PROFILE_OPTIONS,
  normaliseTicker,
  profileOptions,
  RESEARCH_SECTIONS,
  researchCitations,
  researchIndex,
  sectionLabel,
  SECTION_IDS,
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
