import { describe, expect, it } from "vitest";

import { concentrationOf, concentrationParts, graphLayout, liveSankey, portfolioSankey, shockScenario, type PortfolioDependencies, type SEdge } from "@/lib/disclosure/dependencies";
import { sampleClaims, sampleDependencies, samplePortfolio } from "@/lib/disclosure/fixtures-dependencies";

const END = Date.parse("2026-06-30T23:59:59Z");

describe("concentration", () => {
  const c = concentrationOf(sampleClaims(END));
  const total = c.find((x) => x.denominator === "total_revenue")!;

  it("counts only confirmed claims and leaves an unknown remainder", () => {
    expect(total.entries.map((e) => e.label)).toEqual(["Borealis Systems", "Cinder Retail", "Customer (not named)"]);
    expect(Number(total.lower_bound)).toBeCloseTo(0.47);
    // the group figure (38%) is a floor, not an addition; the single-party sum (47%) is higher
    expect(Number(total.reported_floor)).toBeCloseTo(0.47);
    expect(Number(total.unknown_remainder)).toBeCloseTo(0.53);
    expect(total.suggested_not_counted).toBe(2);
  });

  it("never mixes bases", () => {
    expect(c.map((x) => x.denominator).sort()).toEqual(["segment_revenue", "total_revenue"]);
    expect(c.find((x) => x.denominator === "segment_revenue")!.entries).toHaveLength(1);
  });

  it("draws the remainder as its own part", () => {
    const parts = concentrationParts(total);
    expect(parts.at(-1)!.kind).toBe("remainder");
    expect(parts.reduce((a, p) => a + p.share, 0)).toBeCloseTo(1);
  });

  it("replays status: a claim confirmed in April was a suggestion in March", () => {
    const march = sampleClaims(Date.parse("2026-03-15T00:00:00Z"));
    expect(march.find((x) => x.id === 2)!.status).toBe("suggested");
    expect(sampleClaims(END).find((x) => x.id === 2)!.status).toBe("confirmed");
    expect(sampleClaims(Date.parse("2026-01-01T00:00:00Z"))).toEqual([]);
  });
});

describe("graph and portfolio", () => {
  it("points money flow, keeps unnamed customers apart, and drops rejected claims", () => {
    const g = graphLayout("ACME", sampleClaims(END));
    const sup = g.edges.find((e) => e.id === 7)!;
    expect(sup.from).toBe("supplier:7");
    expect(sup.to).toBe("issuer");
    const sale = g.edges.find((e) => e.id === 1)!;
    expect(sale.from).toBe("issuer");
    expect(g.edges.find((e) => e.id === 8)!.share).toBeNull();
    expect(g.edges.find((e) => e.id === 9)!.label).toBe("contract award");
    expect(g.nodes.find((n) => n.id === "customer:3")!.label).toBe("Customer (not named)");
  });

  it("draws only confirmed numeric edges and a not-disclosed remainder", () => {
    const s = portfolioSankey(samplePortfolio(END));
    expect(s.rows.every((r) => r.value > 0)).toBe(true);
    expect(s.rows.some((r) => r.counterparty === "Dune Mobility")).toBe(false); // suggested
    expect(s.nodes.some((n) => n.label === "Not disclosed")).toBe(true);
    const acme = s.rows.filter((r) => r.holding === "ACME").reduce((a, r) => a + r.value, 0);
    expect(acme).toBeCloseTo(0.3 * (0.22 + 0.14 + 0.11));
    // the same customer name in two holdings is one node; two unnamed customers are two nodes
    expect(s.nodes.filter((n) => n.label === "Borealis Systems")).toHaveLength(1);
    expect(s.nodes.filter((n) => n.label.endsWith("unnamed customer"))).toHaveLength(2);
  });
});

describe("scenario", () => {
  const edges: SEdge[] = [
    { id: 1, supplier: "SUP", customer: "O", share: 0.3, status: "confirmed", knownMs: 0 },
    { id: 2, supplier: "SUB", customer: "SUP", share: 0.5, status: "confirmed", knownMs: 0 },
    { id: 3, supplier: "SUP", customer: "X", share: null, status: "confirmed", knownMs: 0 },
    { id: 4, supplier: "NO", customer: "O", share: 0.9, status: "suggested", knownMs: 0 },
  ];

  it("gives lower and upper bounds, two hops, and lists qualitative edges", () => {
    const s = shockScenario(edges, "O", 0.2, END);
    const sup = s.impacts.find((i) => i.entity === "SUP")!;
    expect(sup.lower).toBeCloseTo(0.06);
    expect(sup.upper).toBeCloseTo(0.06 + 0.7 * 0.2);
    expect(s.impacts.find((i) => i.entity === "SUB")!.hop).toBe(2);
    expect(s.impacts.some((i) => i.entity === "NO")).toBe(false);
    expect(s.nonQuantified.map((e) => e.id)).toEqual([3]);
    expect(shockScenario(edges, "O", 0.2, END, 1).impacts.map((i) => i.entity)).toEqual(["SUP"]);
  });

  it("matches the sample view's backend-shaped scenario", () => {
    const v = sampleDependencies(END, 0.2);
    expect(v.scenarios[0].impacts.map((i) => i.entity)).toEqual(["ACME", "CNDR_SUP"]);
    expect(v.scenarios[0].non_quantified).toHaveLength(1);
  });
});

describe("live portfolio flows", () => {
  const p: PortfolioDependencies = {
    as_of: "2026-06-30T00:00:00Z",
    holdings: 3,
    covered_weight: "0.5",
    uncovered: ["NOCLAIM"],
    flows: [
      { holding: "ACME", counterparty: "Borealis", weight: "0.3", share: "0.22", exposure: "0.066", period: "2025-12-31", claim_id: 1 },
      { holding: "ORBT", counterparty: "Borealis", weight: "0.2", share: "0.35", exposure: "0.07", period: "2025-12-31", claim_id: 2 },
    ],
    by_counterparty: { Borealis: "0.136" },
    notes: [],
  };
  it("joins the same named customer across holdings into one node", () => {
    const g = liveSankey(p);
    expect(g.nodes.filter((n) => n.label === "Borealis")).toHaveLength(1);
    expect(g.links).toHaveLength(2);
  });
  it("draws nothing for a holding with no flow: unknown is not zero", () => {
    expect(liveSankey(p).nodes.some((n) => n.label === "NOCLAIM")).toBe(false);
  });
});
