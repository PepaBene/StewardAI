import { describe, expect, it } from "vitest";

import type { EscalationBody } from "@/lib/api/client";

import {
  escalationHref,
  escalationsForTicker,
  escalationStatus,
  formatMovePct,
  parsePriceMove,
  noteCitationPreview,
  sentenceCitations,
  thesisLink,
} from "./escalation";

const body: EscalationBody = {
  ticker: "AAPL_US_EQ",
  trigger: "story",
  cluster_id: 12,
  event_type: "regulatory_legal",
  severity: 2,
  headline: "EU fines Apple",
  sentences: [{ text: "The EU fined Apple.", source_urls: ["https://ec.europa.eu/a", "https://reuters.com/b", "https://elsewhere.org/c"] }],
  kpis: [{ id: "services-growth", description: "Services revenue growth" }],
  exit_conditions: [],
  none_applies: false,
  thesis_link: "KPI services-growth",
  rationale: "The fine touches services.",
  sources: [
    { url: "https://ec.europa.eu/a", outlet: "ec.europa.eu", tier: 1, headline: "Commission decision", kind: "story" },
    { url: "https://reuters.com/b", outlet: "reuters.com", tier: 2, headline: null, kind: "search" },
  ],
  dropped_sentences: 0,
  writer: "claude-sonnet-5",
  price_move: null,
};

describe("escalations", () => {
  it("links to the list and the note", () => {
    expect(escalationHref(5)).toBe("/escalations/5");
    expect(escalationsForTicker("BRK.B")).toBe("/escalations?ticker=BRK.B");
  });

  it("numbers each sentence's citations by the note's source list", () => {
    expect(sentenceCitations(body.sentences[0], body.sources)).toEqual([
      { url: "https://ec.europa.eu/a", number: 1, title: "Commission decision · ec.europa.eu" },
      { url: "https://reuters.com/b", number: 2, title: "reuters.com" },
      { url: "https://elsewhere.org/c", number: null, title: "https://elsewhere.org/c" },
    ]);
  });

  it("says who assessed the note, or why nobody did", () => {
    expect(escalationStatus(body)).toEqual({ kind: "assessed", label: "Assessed by claude-sonnet-5" });
    expect(escalationStatus({ writer: null, thesis_link: "not assessed: no Anthropic API key", rationale: "" })).toEqual({
      kind: "not_assessed",
      label: "Not assessed",
      reason: "no Anthropic API key",
    });
  });

  it("names the KPIs and exit conditions touched, or states that none applies", () => {
    expect(thesisLink(body)).toEqual({
      kind: "linked",
      kpis: [{ id: "services-growth", description: "Services revenue growth" }],
      exitConditions: [],
    });
    expect(thesisLink({ ...body, kpis: [], none_applies: true, thesis_link: "none: no thesis KPI or exit condition applies" })).toEqual({
      kind: "none",
      text: "No thesis KPI or exit condition applies",
    });
    expect(thesisLink({ ...body, kpis: [], none_applies: true, thesis_link: "none: the holding has no thesis" }).kind).toBe("none");
  });

  it("reads the price move that triggered a note", () => {
    expect(parsePriceMove({ date: "2026-09-24", move_pct: -9.24, symbol: "aapl.us" })).toEqual({
      date: "2026-09-24",
      movePct: -9.24,
      symbol: "aapl.us",
    });
    expect(parsePriceMove({ date: "2026-09-24" })).toBeNull();
    expect(parsePriceMove(null)).toBeNull();
    expect(formatMovePct(9.24, "en-US")).toBe("+9.2 %");
    expect(formatMovePct(-8.4, "en-US")).toBe("-8.4 %");
  });
});

describe("impact-note citation preview", () => {
  const sources = [
    { ...body.sources[0], snippet: "The Commission fined Apple EUR 1.8 billion." },
    { ...body.sources[1], snippet: null },
  ];

  it("shows the stored snippet a sentence was checked against, with the tier", () => {
    expect(noteCitationPreview(sources, "https://ec.europa.eu/a")).toEqual({
      kind: "note",
      title: "Commission decision",
      snippet: "The Commission fined Apple EUR 1.8 billion.",
      tier: 1,
      tierLabel: "Tier 1",
      source: "ec.europa.eu",
      url: "https://ec.europa.eu/a",
    });
    expect(noteCitationPreview(sources, "https://reuters.com/b")).toMatchObject({ title: "reuters.com", snippet: null, tierLabel: "Tier 2" });
    expect(noteCitationPreview(sources, "https://elsewhere.org/c")).toBeNull();
    expect(noteCitationPreview([{ url: "https://x.org/p", kind: "price" }], "https://x.org/p")).toMatchObject({ tier: null, tierLabel: "Tier unknown", title: "x.org" });
  });
});
