import { describe, expect, it } from "vitest";

import type { DisputedStory } from "@/lib/api/client";

import {
  applyResolution,
  disputedStoryHref,
  noteError,
  resolutionBody,
  resolutionState,
  sortSources,
  sourceOutlet,
  splitDisputed,
} from "./disputed";

function story(id: number, change: Partial<DisputedStory> = {}): DisputedStory {
  return {
    id,
    ticker: "AAPL_US_EQ",
    event_type: "m_and_a",
    severity: 3,
    headline: `Story ${id}`,
    status_reason: "contradicted by a tier-1 source",
    first_seen: "2026-09-24T08:00:00Z",
    last_seen: `2026-09-24T1${id}:00:00Z`,
    claim: [],
    contradicting: [],
    supporting: [],
    note: null,
    resolution: null,
    resolution_note: null,
    resolved_at: null,
    verdicts_frozen: true,
    ...change,
  };
}

describe("disputed stories", () => {
  it("names each source's outlet, from the URL when not stored", () => {
    expect(sourceOutlet({ outlet: "reuters.com", url: "https://x.y/" })).toBe("reuters.com");
    expect(sourceOutlet({ outlet: null, url: "https://www.sec.gov/Archives/1" })).toBe("sec.gov");
    expect(sourceOutlet({ outlet: null, url: "not a url" })).toBe("not a url");
  });

  it("orders sources by tier, then by publication time", () => {
    const sorted = sortSources([
      { url: "c", tier: 2, published_at: "2026-09-24T09:00:00Z" },
      { url: "d", tier: 2, published_at: null },
      { url: "b", tier: 2, published_at: "2026-09-24T08:00:00Z" },
      { url: "a", tier: 1, published_at: null },
    ]);
    expect(sorted.map((s) => s.url)).toEqual(["a", "b", "c", "d"]);
  });

  it("lists open stories before resolved ones", () => {
    const { open, resolved } = splitDisputed([
      story(1),
      story(2, { resolution: "false", resolved_at: "2026-09-25T09:00:00Z" }),
      story(3),
    ]);
    expect(open.map((s) => s.id)).toEqual([3, 1]);
    expect(resolved.map((s) => s.id)).toEqual([2]);
  });

  it("describes the resolution", () => {
    expect(resolutionState(story(1)).kind).toBe("open");
    expect(
      resolutionState(story(1, { resolution: "confirmed", resolution_note: "Company confirmed", resolved_at: "2026-09-25T09:00:00Z" })),
    ).toEqual({ kind: "confirmed", label: "Resolved: confirmed by you", note: "Company confirmed", at: "2026-09-25T09:00:00Z" });
    expect(resolutionState(story(1, { resolution: "false", resolved_at: "2026-09-25T09:00:00Z" })).label).toContain("false");
  });

  it("builds the request body and checks the note", () => {
    expect(resolutionBody("confirmed", "  ")).toEqual({ resolution: "confirmed", note: null });
    expect(resolutionBody("false", " Denied by the company ")).toEqual({ resolution: "false", note: "Denied by the company" });
    expect(noteError("x".repeat(500))).toBeNull();
    expect(noteError("x".repeat(501))).toContain("500");
  });

  it("applies the API's updated story", () => {
    const updated = story(2, { resolution: "false", resolved_at: "2026-09-25T09:00:00Z", verdicts_frozen: false });
    const list = applyResolution([story(1), story(2)], [updated]);
    expect(list[1]).toBe(updated);
    expect(list[0].id).toBe(1);
    expect(disputedStoryHref(7)).toBe("/news/disputed#story-7");
  });
});
