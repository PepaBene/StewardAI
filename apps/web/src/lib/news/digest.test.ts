import { describe, expect, it } from "vitest";

import type { DigestBody } from "@/lib/api/client";

import { allSentences, citationLinks, sourceNumbers } from "./digest";

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
