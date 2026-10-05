import { describe, expect, it } from "vitest";

import { cellState, coverageGrid, parseCoverage, parseDomainGates, parseShadow, parseSignals, parseZeroCost } from "./verification";

describe("coverage grid", () => {
  const rows = parseCoverage([
    { jurisdiction: "DE", domain: "macro", metric: "cpi", observations: 10, unknown_values: 0 },
    { jurisdiction: "US", domain: "macro", metric: "cpi", observations: 5, unknown_values: 2 },
    { jurisdiction: "DE", domain: "fund", metric: "ter", observations: 0, unknown_values: null },
  ]);
  it("marks missing rows as no coverage, never ok", () => {
    const g = coverageGrid(rows);
    expect(g.jurisdictions).toEqual(["DE", "US"]);
    expect(g.rows[0]).toMatchObject({ label: "macro / cpi", cells: ["ok", "partial"] });
    expect(g.rows[1].cells).toEqual(["none", "none"]);
    expect(cellState(undefined)).toBe("none");
  });
  it("caps rows and reports how many are hidden", () => {
    expect(coverageGrid(rows, 1).hiddenMetrics).toBe(1);
  });
});

describe("gates", () => {
  it("keeps unmeasured precision null and lists unpublishable signals first", () => {
    const s = parseSignals({ b: { publishable: true, sample_size: 10, evidence_precision: 0.9 }, a: { publishable: false, evidence_precision: null, reasons: ["no audit"] } });
    expect(s.map((x) => x.name)).toEqual(["a", "b"]);
    expect(s[0].precision).toBeNull();
    expect(s[0].sampleSize).toBeNull();
  });
  it("parses domains, conclusions and the zero-cost gate", () => {
    const d = parseDomainGates({ domains: { fx: { allowed: false, samples: 3, reasons: ["few"] } }, conclusions: { c: { domains: ["fx"], allowed: false } } })!;
    expect(d.domains[0]).toMatchObject({ domain: "fx", allowed: false, samples: 3, failures: null });
    expect(d.conclusions[0].domains).toEqual(["fx"]);
    const z = parseZeroCost({ passed: true, checked: 4, warnings_by_code: { a: 1, b: 3 }, findings: [] })!;
    expect(z.warningCodes[0].code).toBe("b");
    expect(z.violations).toBeNull();
    expect(parseZeroCost(null)).toBeNull();
  });
  it("parses a shadow report and keeps missing ratios null", () => {
    const s = parseShadow({ generated_at: "2026-10-01", report: { sign_off_ready: false, blockers: ["x"], providers: [{ provider: "p", value_added_ratio: null, failure_rate: 0.1 }] } })!;
    expect(s.providers[0].valueAddedRatio).toBeNull();
    expect(s.providers[0].failureRate).toBe(0.1);
    expect(s.blockers).toEqual(["x"]);
    expect(parseShadow({})).toBeNull();
  });
});
