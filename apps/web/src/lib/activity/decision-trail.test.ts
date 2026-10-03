import { describe, expect, it } from "vitest";

import { appendPage, atEndOfTrail, canLoadOlder, evidenceFields, settleOlderPage, outcomeMeta, summarize, validTicker, type TrailLoad, type TrailRow } from "./decision-trail";

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

describe("fix pass: older-decision paging", () => {
  const loaded: TrailLoad = { kind: "ok", rows: [row(5, "alerted")], next: 5, paging: true };
  it("F1-04: a late page from an earlier filter does not replace the new filter's rows", () => {
    const fresh: TrailLoad = { kind: "loading" };
    expect(settleOlderPage(fresh, 1, 2, { page: { rows: [row(4, "alerted")], next: null } })).toBe(fresh);
    const newFilterRows: TrailLoad = { kind: "ok", rows: [row(9, "raised")], next: null, paging: false };
    expect(settleOlderPage(newFilterRows, 1, 2, { page: { rows: [row(4, "alerted")], next: null } })).toBe(newFilterRows);
  });
  it("F1-04: the current filter's page is appended and a failure keeps rows and cursor for a retry", () => {
    const ok = settleOlderPage(loaded, 3, 3, { page: { rows: [row(4, "alerted")], next: 4 } });
    expect(ok).toMatchObject({ kind: "ok", next: 4, paging: false });
    expect((ok as { rows: TrailRow[] }).rows.map((r) => r.id)).toEqual([5, 4]);
    const failed = settleOlderPage(loaded, 3, 3, { error: "HTTP 500" });
    expect(failed).toMatchObject({ kind: "error", next: 5 });
    expect(canLoadOlder(failed)).toBe(true);
    expect(atEndOfTrail(failed)).toBe(false);
    expect(atEndOfTrail({ kind: "ok", rows: [], next: null, paging: false })).toBe(true);
  });
});
