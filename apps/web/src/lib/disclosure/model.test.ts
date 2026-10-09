import { describe, expect, it } from "vitest";

import { SAMPLE_CLAIMS, SAMPLE_COVERAGE, SAMPLE_FILINGS, SAMPLE_OBSERVATIONS, SAMPLE_SOURCES, sampleProvenance } from "./fixtures";
import {
  claimsAt,
  COVERAGE_STATES,
  coverageInfo,
  dateRows,
  endOfDayMs,
  filingsAt,
  filingStatus,
  formatValue,
  formFamily,
  observationsAt,
} from "./model";
import { cutoffFromX, layoutRail, timeTicks } from "./rail";

const BEFORE = endOfDayMs("2026-01-01");
const BETWEEN = endOfDayMs("2026-03-15");
const AFTER = endOfDayMs("2026-06-01");

describe("as-of replay (client side, mirrors the backend test)", () => {
  it("shows nothing before the first filing was known", () => {
    expect(filingsAt(SAMPLE_FILINGS, endOfDayMs("2025-01-01"))).toEqual([]);
    expect(observationsAt(SAMPLE_OBSERVATIONS, endOfDayMs("2025-01-01"))).toEqual([]);
  });

  it("hides the amendment, and the link to it, until it was known", () => {
    const mid = filingsAt(SAMPLE_FILINGS, BETWEEN);
    expect(mid.map((f) => f.version_id)).toEqual([103, 102, 101]);
    expect(mid.every((f) => f.amends_version_id === null)).toBe(true);
    expect(observationsAt(SAMPLE_OBSERVATIONS, BETWEEN).map((o) => o.id)).toEqual([1, 3, 4]);
  });

  it("after the amendment, the restated value replaces the original", () => {
    const late = observationsAt(SAMPLE_OBSERVATIONS, AFTER);
    const revenue = late.filter((o) => o.metric === "revenue" && o.segment === null);
    expect(revenue.map((o) => o.id)).toEqual([2]);
    expect(revenue[0].value).toBe("950000000");
    const amendment = filingsAt(SAMPLE_FILINGS, AFTER).find((f) => f.version_id === 104);
    expect(amendment?.amends_version_id).toBe(102);
  });

  it("a machine suggestion is never presented as verified", () => {
    const claims = claimsAt(SAMPLE_CLAIMS, AFTER);
    expect(claims).toHaveLength(1);
    expect(claims[0].review_state).toBe("machine_suggested");
    expect(claimsAt(SAMPLE_CLAIMS, BEFORE)).toEqual([]);
  });

  it("never invents a row that was not known by the cutoff", () => {
    for (const cutoff of [BEFORE, BETWEEN, AFTER]) {
      for (const f of filingsAt(SAMPLE_FILINGS, cutoff)) expect(Date.parse(f.known_at)).toBeLessThanOrEqual(cutoff);
      for (const o of observationsAt(SAMPLE_OBSERVATIONS, cutoff)) expect(Date.parse(o.known_at)).toBeLessThanOrEqual(cutoff);
    }
  });
});

describe("dates", () => {
  it("explains every missing date instead of leaving it blank", () => {
    const annual = SAMPLE_FILINGS.find((f) => f.version_id === 102)!;
    const rows = Object.fromEntries(dateRows(annual).map((r) => [r.key, r]));
    expect(rows.accepted_at.value).toBeNull();
    expect(rows.accepted_at.note).toContain("end of filing day");
    expect(rows.published_at.note).toContain("no separate publication time");
    expect(rows.known_at.value).toBe("2026-02-20T23:59:59Z");
    for (const r of dateRows(annual)) expect(r.value !== null || (r.note ?? "").length > 0).toBe(true);
  });

  it("classifies status: amendment, quarantined, current", () => {
    const status = Object.fromEntries(SAMPLE_FILINGS.map((f) => [f.version_id, filingStatus(f)]));
    expect(status[104]).toBe("amendment");
    expect(status[106]).toBe("quarantined");
    expect(status[101]).toBe("current");
  });
});

describe("families and states", () => {
  it("maps forms to lanes, amendments with their base form", () => {
    expect(formFamily("10-K/A")).toBe("annual");
    expect(formFamily("20-F")).toBe("annual");
    expect(formFamily("10-Q")).toBe("interim");
    expect(formFamily("8-K")).toBe("current");
    expect(formFamily("EX-99.2")).toBe("current");
    expect(formFamily("SC 13D/A")).toBe("ownership");
    expect(formFamily("TR-1")).toBe("ownership");
    expect(formFamily("DEF 14A")).toBe("registration");
    expect(formFamily(null)).toBe("other");
  });

  it("every state the backend can send has a label and an explanation", () => {
    for (const state of [
      "not_disclosed", "not_covered", "paid_only", "authentication_failed", "retrieval_failed",
      "parse_failed", "unresolved_identity", "under_review", "verified", "stale",
    ]) {
      expect(COVERAGE_STATES[state as keyof typeof COVERAGE_STATES].help.length).toBeGreaterThan(10);
    }
    expect(coverageInfo("something_new").label).toBe("something_new");
    expect(new Set(SAMPLE_COVERAGE.map((c) => c.state)).size).toBe(10); // the sample shows them all
  });

  it("a missing value is never rendered as zero", () => {
    expect(formatValue({ value: null, value_state: "not_disclosed", unit: "USD", text_value: null, currency: "USD" })).toBe("Not disclosed");
    expect(formatValue({ value: null, value_state: "null", unit: "USD", text_value: null, currency: "USD" })).toBe("No value reported");
    expect(formatValue({ value: "0", value_state: "reported", unit: "USD", text_value: null, currency: "USD" })).toBe("0 USD");
    expect(formatValue({ value: "950000000", value_state: "reported", unit: "USD", text_value: null, currency: "USD" })).toBe("950,000,000 USD");
  });

  it("sample sources carry no enabled source and keep untested ones distinct", () => {
    expect(SAMPLE_SOURCES.some((s) => s.enabled)).toBe(false);
    expect(SAMPLE_SOURCES.filter((s) => s.access_state === "untested").map((s) => s.source_key)).toContain("sedarplus.portal");
  });

  it("sample provenance resolves a value to its filing and version chain", () => {
    const p = sampleProvenance("observation", 2)!;
    expect(p.external_id).toBe("0000001234-26-000012");
    expect(p.evidence.quote).toBe("950");
    expect(sampleProvenance("observation", 999)).toBeNull();
  });
});

describe("rail geometry", () => {
  const rail = layoutRail(SAMPLE_FILINGS, BETWEEN);

  it("places filings in lanes by family and marks which are known at the cutoff", () => {
    expect(rail.lanes.map((l) => l.family)).toEqual(["annual", "interim", "current"]);
    const known = rail.marks.filter((m) => m.known).map((m) => m.id).sort();
    expect(known).toEqual([101, 102, 103]);
    expect(rail.marks.find((m) => m.id === 104)?.known).toBe(false);
  });

  it("keeps the picture stable: the domain covers filings after the cutoff", () => {
    const early = layoutRail(SAMPLE_FILINGS, BEFORE);
    const late = layoutRail(SAMPLE_FILINGS, AFTER);
    expect(early.marks.map((m) => m.x)).toEqual(late.marks.map((m) => m.x));
  });

  it("draws a tail where the filing date precedes the known-at moment, and a link for an amendment", () => {
    const annual = rail.marks.find((m) => m.id === 102)!;
    expect(annual.tailX).not.toBeNull(); // filed 00:00, known 23:59:59 the same day: sub-pixel
    const link = rail.links.find((l) => l.from === 104);
    expect(link?.to).toBe(102);
    expect(link!.fromX).toBeGreaterThan(link!.toX);
  });

  it("inverts the time scale for pointer input and clamps to the plot", () => {
    const x = rail.cutoffX;
    expect(Math.abs(cutoffFromX(rail, x) - BETWEEN)).toBeLessThan(2000);
    expect(cutoffFromX(rail, -50)).toBe(rail.domain[0]);
    expect(cutoffFromX(rail, 99999)).toBe(rail.domain[1]);
  });

  it("makes sensible month and year ticks", () => {
    const ticks = timeTicks(Date.UTC(2025, 0, 15), Date.UTC(2026, 5, 1));
    expect(ticks.find((t) => t.major)?.label).toBe("2026");
    expect(ticks.length).toBeGreaterThan(3);
  });

  it("does not crash on an issuer with no filings", () => {
    const empty = layoutRail([], BETWEEN);
    expect(empty.marks).toEqual([]);
    expect(empty.lanes).toHaveLength(1);
  });
});
