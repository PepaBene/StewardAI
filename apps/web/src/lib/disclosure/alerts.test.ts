import { describe, expect, it } from "vitest";

import { byKnown, splitByState, type DisclosureAlert } from "@/lib/disclosure/alerts";

const alert = (id: number, known: string, ack: string | null = null): DisclosureAlert => ({
  id,
  kind: "insider_trade",
  ticker: "ACME",
  headline: "Dana Reyes bought 20,000 shares",
  detail: {},
  source_url: null,
  known_at: known,
  created_at: known,
  acknowledged_at: ack,
});

describe("alerts", () => {
  it("separates open from acknowledged without dropping either", () => {
    const { open, done } = splitByState([alert(1, "2026-06-01T00:00:00Z"), alert(2, "2026-06-02T00:00:00Z", "2026-06-03T00:00:00Z")]);
    expect(open.map((a) => a.id)).toEqual([1]);
    expect(done.map((a) => a.id)).toEqual([2]);
  });

  it("reads the most recently known alert first", () => {
    expect(byKnown([alert(1, "2026-06-01T00:00:00Z"), alert(2, "2026-06-05T00:00:00Z")]).map((a) => a.id)).toEqual([2, 1]);
  });
});
