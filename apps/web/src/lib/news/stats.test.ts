import { describe, expect, it } from "vitest";

import { EMPTY_FILTERS } from "@/lib/news/feed-filters";

import {
  activePreset,
  categorySegments,
  healthSummary,
  presetFilters,
  severityBlocks,
  severityColumns,
  sourceBars,
  storyTime,
  timelineTitle,
  trustBars,
  type SourceHealth,
} from "./stats";

describe("severityColumns", () => {
  it("stacks each day by severity band, folding ignored and unclassified into other", () => {
    const cols = severityColumns({
      per_day: [
        { date: "2026-09-14", by_severity: { "0": 1, "1": 2, "2": 3, "3": 4 }, unclassified: 2 },
        { date: "2026-09-15", by_severity: { "0": 0, "1": 0, "2": 0, "3": 0 }, unclassified: 0 },
      ],
    });
    expect(cols[0]).toEqual({ date: "2026-09-14", label: "14", alert: 4, highlight: 3, digest: 2, other: 3, total: 12 });
    expect(cols[1].total).toBe(0);
  });

  it("treats missing or negative counts as zero", () => {
    const [col] = severityColumns({ per_day: [{ date: "2026-10-01", by_severity: { "3": -1 }, unclassified: 0 }] });
    expect(col).toMatchObject({ label: "1", alert: 0, total: 0 });
  });
});

describe("timelineTitle", () => {
  it("pluralises", () => {
    expect(timelineTitle({ total: 18, days: 14 })).toBe("18 stories, 14 days");
    expect(timelineTitle({ total: 1, days: 1 })).toBe("1 story, 1 day");
  });
});

describe("categorySegments", () => {
  it("labels event types, most first, dropping empty ones", () => {
    expect(
      categorySegments({
        by_event_type: [
          { event_type: "m_and_a", count: 1 },
          { event_type: "guidance_change", count: 3 },
          { event_type: "other", count: 0 },
        ],
      }),
    ).toEqual([
      { label: "Guidance change", value: 3 },
      { label: "M&A", value: 1 },
    ]);
  });
});

describe("categorySegments folding", () => {
  it("folds the tail past the limit into one segment", () => {
    const by_event_type = ["m_and_a", "guidance_change", "other", "central_bank"].map((event_type, i) => ({ event_type, count: 4 - i }));
    expect(categorySegments({ by_event_type }, 3)).toEqual([
      { label: "M&A", value: 4 },
      { label: "Guidance change", value: 3 },
      { label: "2 more types", value: 3 },
    ]);
  });
});

describe("trustBars", () => {
  it("lists the most trusted first, capped", () => {
    const row = (host: string, score: number, useful = 0) => ({
      host,
      score,
      wrong_count: 0,
      useful_count: useful,
      demoted: score < 0.5,
      updated_at: "2026-09-20T00:00:00Z",
    });
    const bars = trustBars([row("a.com", 0.4), row("b.com", 1, 2), row("c.com", 1, 5), row("d.com", 0.9)], 3);
    expect(bars.map((b) => b.host)).toEqual(["c.com", "b.com", "d.com"]);
    expect(trustBars([row("a.com", 0.4)])[0].demoted).toBe(true);
  });
});

describe("healthSummary", () => {
  it("counts healthy feeds among the enabled ones", () => {
    const src = (id: string, enabled: boolean, healthy: boolean): SourceHealth => ({
      state: healthy ? "ok" : "down",
      region: null,
      language: null,
      paywall: "open",
      state_media: false,
      needs_alt: false,
      runs_7d: 0,
      fetched_7d: 0,
      stored_linked_7d: 0,
      stored_wire_7d: 0,
      consecutive_failures: 0,
      backoff_until: null,
      last_success_at: null,
      id,
      name: id,
      type: "rss",
      host: null,
      tier: 1,
      enabled,
      last_fetched_at: null,
      last_item_count: null,
      last_error: null,
      healthy,
    });
    const s = healthSummary({ window_hours: 48, sources: [src("a", true, true), src("b", true, false), src("c", false, false)] });
    expect(s).toMatchObject({ healthy: 1, enabled: 2, total: 3, windowHours: 48 });
    expect(s.failing.map((f) => f.id)).toEqual(["b"]);
  });
});

describe("story row helpers", () => {
  it("fills severity blocks by level", () => {
    expect([null, 0, 1, 2, 3, 7].map(severityBlocks)).toEqual([0, 0, 1, 2, 3, 3]);
  });

  it("draws one bar per outlet up to five", () => {
    expect(sourceBars(2)).toEqual([true, true, false, false, false]);
    expect(sourceBars(9).every(Boolean)).toBe(true);
    expect(sourceBars(0).some(Boolean)).toBe(false);
  });

  it("shows a clock time today, Yday yesterday and a date before", () => {
    const now = new Date("2026-09-27T18:00:00Z");
    expect(storyTime(new Date("2026-09-27T17:42:00Z"), now, "UTC")).toBe("17:42");
    expect(storyTime(new Date("2026-09-26T09:00:00Z"), now, "UTC")).toBe("Yday");
    expect(storyTime(new Date("2026-09-20T09:00:00Z"), now, "UTC")).toBe("20 Sep");
  });
});

describe("presets", () => {
  it("round-trips each preset and recognises custom filters", () => {
    for (const id of ["all", "high", "confirmed"] as const) expect(activePreset(presetFilters(id))).toBe(id);
    expect(activePreset({ ...EMPTY_FILTERS, severities: [3, 2] })).toBe("high");
    expect(activePreset({ ...EMPTY_FILTERS, ticker: "NVDA_US_EQ" })).toBeNull();
  });
});
