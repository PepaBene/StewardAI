import { describe, expect, it } from "vitest";

import { consensusPeriods, dispersion, latestVintage, metricLabel, parseForward } from "./forward";

const payload = {
  consensus_series: [
    {
      metric: "eps",
      fiscal_period: "FY2026",
      provider: "issuer_ir",
      currency: "USD",
      basis: "non-gaap",
      revision_since_previous: "0.05",
      vintages: [
        { known_at: "2026-06-01T00:00:00Z", value: "5.10", low: "4.9", high: "5.4", analyst_count: 18 },
        { known_at: "2026-03-01T00:00:00Z", value: "5.05", low: null, high: null, analyst_count: null },
      ],
    },
  ],
  observations: [
    { id: 1, kind: "published_analyst_consensus", metric: "eps", value: "5.1" },
    { id: 2, kind: "company_guidance", metric: "revenue", fiscal_period: "FY2026", value: "100", low: "98", high: "102" },
    { id: 3, kind: "owner_scenario", metric: "eps", value: "4.5", provider: "owner" },
    { id: 4, kind: "mystery", metric: "eps" },
  ],
};

describe("forward data", () => {
  it("orders vintages oldest first and keeps missing ranges null", () => {
    const view = parseForward(payload)!;
    const [s] = view.consensus;
    expect(s.vintages.map((v) => v.knownAt.slice(0, 7))).toEqual(["2026-03", "2026-06"]);
    expect(s.vintages[0]).toMatchObject({ low: null, high: null, analystCount: null });
    expect(s.revision).toBeCloseTo(0.05);
    expect(latestVintage(s)!.analystCount).toBe(18);
  });

  it("keeps guidance, scenarios and estimates apart from consensus and drops unknown kinds", () => {
    const view = parseForward(payload)!;
    expect(view.others.map((o) => o.kind)).toEqual(["company_guidance", "owner_scenario"]);
    expect(consensusPeriods(view)).toEqual(["FY2026"]);
  });

  it("computes dispersion only with a range and a non-zero value", () => {
    const view = parseForward(payload)!;
    const [s] = view.consensus;
    expect(dispersion(latestVintage(s))).toBeCloseTo(0.5 / 5.1);
    expect(dispersion(s.vintages[0])).toBeNull();
    expect(dispersion(null)).toBeNull();
  });

  it("handles an empty or malformed payload", () => {
    expect(parseForward(null)).toBeNull();
    expect(parseForward({})).toEqual({ consensus: [], others: [] });
    expect(metricLabel("eps_diluted")).toBe("Eps diluted");
  });
});
