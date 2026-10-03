import { describe, expect, it } from "vitest";

import { classMeta, classTotals, pdmrState } from "./insider";
import type { InsiderTransactionRow } from "./types";

function row(cls: string | null, shares: number | string | null, over: Partial<InsiderTransactionRow> = {}): InsiderTransactionRow {
  return { economic_class: cls, shares, superseded_by: null, ...over } as unknown as InsiderTransactionRow;
}

describe("classTotals", () => {
  it("keeps open-market trades apart from grants, gifts and tax withholding", () => {
    const { totals } = classTotals([row("open_market_buy", 100), row("grant_award", "500"), row("tax_withholding", 40), row("open_market_buy", 50), row("gift", 10)]);
    const byClass = Object.fromEntries(totals.map((t) => [t.economicClass, t]));
    expect(byClass.open_market_buy).toMatchObject({ rows: 2, shares: 150, group: "discretionary" });
    expect(byClass.grant_award.group).toBe("compensation");
    expect(byClass.gift.group).toBe("non_market");
    expect(totals[0].economicClass).toBe("open_market_buy");
  });
  it("does not turn a missing share count into zero", () => {
    const { totals } = classTotals([row("open_market_sell", null)]);
    expect(totals[0].shares).toBeNull();
    expect(totals[0].rowsWithoutShares).toBe(1);
  });
  it("leaves superseded rows out and counts them", () => {
    const { totals, superseded } = classTotals([row("open_market_buy", 100, { superseded_by: "acc-2" }), row("open_market_buy", 90)]);
    expect(totals[0].shares).toBe(90);
    expect(superseded).toBe(1);
  });
  it("labels unknown classes as other", () => {
    expect(classMeta("mystery").group).toBe("other");
    expect(classMeta(null).label).toBe("Unclassified");
  });
});

describe("pdmrState", () => {
  it("maps access states and treats attempts without a parse as partial", () => {
    expect(pdmrState({ state: "available", detail: null, parsed: 0, attempted: 3, by_status: {} })).toBe("partial");
    expect(pdmrState({ state: "available", detail: null, parsed: 2, attempted: 3, by_status: {} })).toBe("verified");
    expect(pdmrState({ state: "access_unverified", detail: null, parsed: 0, attempted: 0, by_status: {} })).toBe("access_unverified");
    expect(pdmrState({ state: "temporarily_failed", detail: null, parsed: 0, attempted: 1, by_status: {} })).toBe("unavailable");
  });
});
