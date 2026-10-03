import { describe, expect, it } from "vitest";

import { appendPage, evidenceFields, outcomeMeta, summarize, validTicker, type TrailRow } from "./decision-trail";

const row = (id: number, outcome: string): TrailRow => ({
  id, created_at: "2026-10-01T00:00:00Z", rule: "credit_stress", scope: "s", ticker: null, outcome,
  severity: null, reason: "r", evidence: {}, news_item_id: null, job_id: null,
});

describe("decision trail", () => {
  it("explains known outcomes and tolerates unknown ones", () => {
    expect(outcomeMeta("throttled").kind).toBe("suppressed");
    expect(outcomeMeta("blocked_by_gate").kind).toBe("provisional");
    expect(outcomeMeta("brand_new").label).toBe("brand new");
  });
  it("renders evidence: gate, safe links, null and nested values", () => {
    const f = evidenceFields({
      gate: { name: "reconciliation", allowed: false, reason: "no audit" },
      source_url: "javascript:alert(1)",
      other_url: 1,
      value: 4.2,
      previous_value: null,
      nested: { a: 1 },
      list: [1, 2],
    });
    expect(f.find((x) => x.key === "gate")?.text).toBe("reconciliation: closed (no audit)");
    expect(f.find((x) => x.key === "source_url")?.href).toBeUndefined();
    expect(f.find((x) => x.key === "previous_value")?.text).toBe("none");
    expect(f.find((x) => x.key === "nested")?.text).toBe("details");
    expect(f.find((x) => x.key === "list")?.text).toBe("2 items");
    expect(evidenceFields({ source_url: "https://x.org/a" })[0].href).toBe("https://x.org/a");
  });
  it("validates the ticker filter and de-duplicates pages", () => {
    expect(validTicker(" AAPL ")).toBe("AAPL");
    expect(validTicker("a b")).toBeNull();
    expect(validTicker("")).toBeNull();
    expect(appendPage([row(3, "raised"), row(2, "raised")], [row(2, "raised"), row(1, "baseline")]).map((r) => r.id)).toEqual([3, 2, 1]);
  });
  it("summarises by kind", () => {
    expect(summarize([row(1, "raised"), row(2, "throttled"), row(3, "baseline"), row(4, "blocked_by_gate")])).toEqual({
      alerted: 1, suppressed: 1, informational: 1, provisional: 1,
    });
  });
});
