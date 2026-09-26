import { describe, expect, it } from "vitest";

import type { ReportHit } from "@/lib/api/client";

import { parseHighlights, plainExcerpt, reportHitHref, searchFromParams, searchHref, sortHits } from "./search";

const hit = (over: Partial<ReportHit>): ReportHit => ({
  id: 1,
  type: "digest",
  period: "daily",
  ticker: null,
  profile: null,
  title: "Daily digest",
  created_at: "2026-09-01T07:00:00Z",
  excerpt: "",
  ...over,
});

describe("report search: highlighted excerpts", () => {
  it("splits the <b> markers into highlighted segments", () => {
    expect(parseHighlights("Revenue of <b>AAPL</b> rose; <b>AAPL</b> guidance")).toEqual([
      { text: "Revenue of ", highlight: false },
      { text: "AAPL", highlight: true },
      { text: " rose; ", highlight: false },
      { text: "AAPL", highlight: true },
      { text: " guidance", highlight: false },
    ]);
  });

  it("keeps every other tag as literal text, never as HTML", () => {
    expect(parseHighlights('<script>x</script> <B>hit</B> <img src=x onerror="y">')).toEqual([
      { text: "<script>x</script> ", highlight: false },
      { text: "hit", highlight: true },
      { text: ' <img src=x onerror="y">', highlight: false },
    ]);
  });

  it("handles unclosed, stray and adjacent markers", () => {
    expect(parseHighlights("a <b>b")).toEqual([
      { text: "a ", highlight: false },
      { text: "b", highlight: true },
    ]);
    expect(parseHighlights("a</b> b")).toEqual([{ text: "a b", highlight: false }]);
    expect(parseHighlights("<b>new</b><b>s</b>")).toEqual([{ text: "news", highlight: true }]);
    expect(parseHighlights("")).toEqual([]);
    expect(plainExcerpt("x <b>y</b> z")).toBe("x y z");
  });
});

describe("report search: links and query", () => {
  it("opens each hit on the page of its type", () => {
    expect(reportHitHref(hit({ id: 3, type: "digest" }))).toBe("/reports/3");
    expect(reportHitHref(hit({ id: 4, type: "research" }))).toBe("/research/4");
    expect(reportHitHref(hit({ id: 5, type: "review" }))).toBe("/reviews/5");
    expect(reportHitHref(hit({ id: 6, type: "escalation" }))).toBe("/escalations/6");
  });

  it("reads and writes the query and type filters in the URL", () => {
    const params = new URLSearchParams("q=%22free+cash+flow%22&type=review&type=bogus&type=research&type=review");
    const state = searchFromParams(params);
    expect(state).toEqual({ q: '"free cash flow"', types: ["review", "research"] });
    expect(searchHref(state)).toBe("/reports?q=%22free+cash+flow%22&type=review&type=research");
    expect(searchHref({ q: " ", types: [] })).toBe("/reports");
  });

  it("lists hits newest first", () => {
    const hits = [
      hit({ id: 1, created_at: "2026-08-01T00:00:00Z" }),
      hit({ id: 3, created_at: "2026-09-01T00:00:00Z" }),
      hit({ id: 2, created_at: "2026-09-01T00:00:00Z" }),
    ];
    expect(sortHits(hits).map((h) => h.id)).toEqual([3, 2, 1]);
  });
});
