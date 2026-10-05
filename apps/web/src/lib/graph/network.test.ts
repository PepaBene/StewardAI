import { describe, expect, it } from "vitest";

import { cellKey, cellSize, citationStep, parseNetwork, parsePatentTrends, statusCounts } from "./network";

describe("business network", () => {
  const payload = {
    known_as_of: null,
    edges: [
      { id: 1, edge_type: "partner", object_name: "Zeta", status: "suggested" },
      { id: 2, edge_type: "acquirer", object_key: "ACME", status: "confirmed", valid_from: "2025-01-01", evidence_tier: "primary" },
      { id: 3, edge_type: "partner", object_name: "Alpha", status: "disputed", decision_reason: "conflicts with filing" },
      { id: 4, edge_type: "partner", status: "weird" },
    ],
    events: [{ id: 9, event_type: "merger", event_state: "announced", announced_at: "2026-02-01", status: "confirmed" }],
    patents: [{ family_key: "F1", attributed: true }, { family_key: "F1", attributed: true }, { family_key: "F2", attributed: false }],
    limitations: ["Suggested and disputed edges are candidates."],
  };

  it("puts confirmed first and never promotes an unknown status", () => {
    const n = parseNetwork(payload)!;
    expect(n.edges.map((e) => e.status)).toEqual(["confirmed", "disputed", "suggested", "suggested"]);
    expect(n.edges[2].counterparty).toBe("Unnamed counterparty");
    expect(statusCounts(n.edges)).toEqual({ confirmed: 1, disputed: 1, suggested: 2, rejected: 0 });
    expect(n.events[0]).toMatchObject({ type: "merger", status: "confirmed", date: "2026-02-01" });
  });

  it("counts a patent family once and skips unattributed ones", () => {
    expect(parseNetwork(payload)!.patentFamilies).toBe(1);
    expect(parseNetwork(null)).toBeNull();
  });
});

describe("patent matrix", () => {
  const row = (cpc: string, year: number, families: number, citations = 0) => ({
    cpc,
    year,
    families,
    forward_citations: citations,
    legal_status_counts: { granted: 1, active: 2 },
    expired: 0,
    expiring_within_5y: 1,
    expiry_is_estimated: true,
  });

  it("keeps the busiest codes and the latest years", () => {
    const m = parsePatentTrends([row("H01L", 2020, 5, 10), row("G06F", 2020, 1), row("G06F", 2021, 2), row("A61K", 2019, 9)], 2, 2)!;
    expect(m.rows).toEqual(["A61K", "H01L"]);
    expect(m.years).toEqual([2020, 2021]);
    expect(m.cells.get(cellKey("H01L", 2020))).toMatchObject({ families: 5, citations: 10, granted: 3, expiryEstimated: true });
    expect(m.cells.has(cellKey("A61K", 2019))).toBe(false);
  });

  it("returns an empty matrix for no rows and null for a bad payload", () => {
    expect(parsePatentTrends([])!.rows).toEqual([]);
    expect(parsePatentTrends({})).toBeNull();
  });

  it("sizes by area and shades by relative citations", () => {
    expect(cellSize(0, 10)).toBe(0);
    expect(cellSize(10, 10)).toBe(1);
    expect(cellSize(1, 100)).toBe(0.28);
    expect(citationStep(0, 50)).toBe(0);
    expect(citationStep(10, 50)).toBe(1);
    expect(citationStep(25, 50)).toBe(2);
    expect(citationStep(50, 50)).toBe(3);
    expect(citationStep(5, 0)).toBe(0);
  });
});
