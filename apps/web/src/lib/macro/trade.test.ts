import { describe, expect, it } from "vitest";

import { availableFlows, isWorld, topPartnerShare, tradeDiagram, type TradeRow } from "./trade";

const row = (o: Partial<TradeRow>): TradeRow =>
  ({
    reporter: "276",
    reporter_iso: "DE",
    partner: "842",
    partner_iso: "US",
    flow: "export",
    hs_revision: "HS2022",
    hs_code: "8541",
    period: "2025-01-01",
    trade_value: "100",
    quantity: null,
    quantity_unit: null,
    known_at: "x",
    source_url: "x",
    ...o,
  }) as TradeRow;

describe("trade diagram", () => {
  const rows = [
    row({ trade_value: "900", partner: "0", partner_iso: "W00" }),
    row({ trade_value: "300" }),
    row({ trade_value: "100", partner: "156", partner_iso: "CN" }),
    row({ trade_value: null, partner: "250", partner_iso: "FR" }),
    row({ flow: "import", trade_value: "50", partner: "156", partner_iso: "CN" }),
  ];

  it("keeps the World row out of the flows and null values out as unreported, not zero", () => {
    const d = tradeDiagram(rows, "export")!;
    expect(d.shown).toEqual([{ partner: "US", value: 300 }, { partner: "CN", value: 100 }]);
    expect(d.worldTotal).toBe(900);
    expect(d.unreported).toBe(1);
    expect(d.links.every((l) => l.source === "reporter")).toBe(true);
    expect(topPartnerShare(d)).toBeCloseTo(0.75);
  });

  it("reverses direction for imports and lists only flows that exist", () => {
    const d = tradeDiagram(rows, "import")!;
    expect(d.links[0]).toMatchObject({ source: "p0", target: "reporter" });
    expect(d.nodes.find((n) => n.id === "reporter")!.column).toBe(1);
    expect(availableFlows(rows)).toEqual(["export", "import"]);
    expect(availableFlows([row({ flow: "re_export" })])).toEqual([]);
  });

  it("does not merge hidden partners and returns null when nothing can be drawn", () => {
    const many = Array.from({ length: 11 }, (_, i) => row({ partner: String(i + 1), partner_iso: `P${i}`, trade_value: String(100 - i) }));
    const d = tradeDiagram(many, "export")!;
    expect(d.shown).toHaveLength(8);
    expect(d.hidden).toBe(3);
    expect(tradeDiagram([row({ trade_value: null })], "export")).toBeNull();
    expect(tradeDiagram([], "export")).toBeNull();
    expect(isWorld({ partner: "0", partner_iso: null })).toBe(true);
  });
});
