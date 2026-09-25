import { describe, expect, it } from "vitest";

import type { DigestBody } from "@/lib/api/client";

import {
  allSentences,
  citationLinks,
  formatMacroValue,
  groupHolidays,
  hasStatusSections,
  sortFlags,
  sortMacro,
  sourceNumbers,
  thesisChangeText,
} from "./digest";

const body: DigestBody = {
  kind: "daily",
  window_start: "2026-09-24T10:00:00Z",
  window_end: "2026-09-25T10:00:00Z",
  nothing_material: false,
  headline: "1 material story in the last 24 hours.",
  overview: [{ text: "A busy day.", news_item_ids: [11], fact_ids: [] }],
  sections: [
    {
      ticker: "AAPL_US_EQ",
      name: "Apple",
      summary: [
        { text: "Apple cut guidance.", news_item_ids: [11, 12], fact_ids: [] },
        { text: "Services growth held.", news_item_ids: [], fact_ids: [7] },
      ],
      stories: [],
      verdicts: [],
    },
  ],
  sources: [
    { news_item_id: 11, url: "https://ir.apple.com/a", outlet: "apple.com", tier: 1, headline: "Apple updates outlook", published_at: "2026-09-25T08:00:00Z" },
    { news_item_id: 12, url: "https://www.reuters.com/b", outlet: "reuters.com", tier: 2, headline: "Apple cuts outlook", published_at: "2026-09-25T08:30:00Z" },
  ],
  alerts_sent: 1,
  dropped_sentences: 0,
  writer: "claude-sonnet-5",
};

describe("digest citations", () => {
  it("links every sentence to a news item or fact", () => {
    const numbers = sourceNumbers(body.sources ?? []);
    for (const sentence of allSentences(body)) {
      const links = citationLinks(sentence, body.sources ?? [], numbers);
      expect(links.length).toBeGreaterThan(0);
      for (const link of links) {
        expect(link.href).toMatch(/^(https:\/\/|\/evidence\/\d+$)/);
      }
    }
  });

  it("numbers news sources by their place in the source list", () => {
    const links = citationLinks(body.sections![0].summary![0], body.sources ?? [], sourceNumbers(body.sources ?? []));
    expect(links.map((l) => l.label)).toEqual(["1", "2"]);
    expect(links[1].title).toContain("tier 2");
    const fact = citationLinks(body.sections![0].summary![1], body.sources ?? [], new Map());
    expect(fact).toEqual([expect.objectContaining({ href: "/evidence/7", external: false })]);
  });

  it("ignores ids that are not in the source list", () => {
    expect(citationLinks({ news_item_ids: [99], fact_ids: [] }, body.sources ?? [], new Map())).toEqual([]);
  });
});

describe("digest status sections", () => {
  it("lists open portfolio flags first, by severity", () => {
    const flag = (key: string, severity: number, cleared_at: string | null) => ({
      kind: "band" as const,
      key,
      ticker: null,
      severity,
      detail: key,
      first_seen: "2026-09-20T22:30:00Z",
      cleared_at,
    });
    const sorted = sortFlags([flag("a", 1, null), flag("b", 3, "2026-09-24T22:30:00Z"), flag("c", 3, null), flag("d", 1, null)]);
    expect(sorted.map((f) => f.key)).toEqual(["c", "a", "d", "b"]);
  });

  it("reads a thesis change", () => {
    expect(thesisChangeText({ from_status: "pass", to_status: "breach" })).toBe("pass → breach");
    expect(thesisChangeText({ from_status: null, to_status: "unknown" })).toBe("never judged → unknown");
  });

  it("formats macro figures in their own unit, new releases first", () => {
    expect(formatMacroValue(2.4, "%", "en-US")).toBe("2.4 %");
    expect(formatMacroValue(101.256, "index", "en-US")).toBe("101.26 index");
    expect(formatMacroValue(4.33, null, "en-US")).toBe("4.33");
    const fig = (label: string, isNew: boolean) => ({
      series_id: label,
      label,
      value: 1,
      unit: "%",
      period: "2026-08-01",
      release_date: null,
      url: "https://ec.europa.eu",
      new: isNew,
    });
    expect(sortMacro([fig("b", false), fig("c", true), fig("a", false)]).map((f) => f.label)).toEqual(["c", "a", "b"]);
  });

  it("groups holidays by date", () => {
    const groups = groupHolidays([
      { calendar: "US", date: "2026-11-26", name: "Thanksgiving" },
      { calendar: "XNYS", date: "2026-11-26", name: "Thanksgiving" },
      { calendar: "CZ", date: "2026-10-28", name: "Independence Day" },
    ]);
    expect(groups.map((g) => [g.date, g.entries.map((e) => e.calendar)])).toEqual([
      ["2026-10-28", ["CZ"]],
      ["2026-11-26", ["US", "XNYS"]],
    ]);
  });

  it("knows when there is anything to show", () => {
    expect(hasStatusSections(body)).toBe(false);
    expect(hasStatusSections({ ...body, reviews_due: [{ ticker: "AAPL_US_EQ", review_date: "2026-09-01" }] })).toBe(true);
  });
});
