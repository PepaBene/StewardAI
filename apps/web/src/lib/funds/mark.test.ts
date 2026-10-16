import { describe, expect, it } from "vitest";

import { FUND_AXES, fundMarkFractions, fundMarkMissing, fundMarkPetals, ongoingChargeFrom, type FundMarkInput } from "./mark";

const NONE: FundMarkInput = {
  ongoingChargePct: null,
  holdingsCount: null,
  top10Weight: null,
  yearsOfHistory: null,
  holdingsLagDays: null,
  fieldsHave: null,
  fieldsOf: null,
};

describe("fund mark", () => {
  it("has five axes in five distinct slots", () => {
    expect(FUND_AXES).toHaveLength(5);
    expect(new Set(FUND_AXES.map((a) => a.slot)).size).toBe(5);
  });

  it("leaves unknown axes as gaps, not zeros", () => {
    const petals = fundMarkPetals({ ...NONE, ongoingChargePct: 0.2 });
    expect(petals.filter((p) => p.fraction === null)).toHaveLength(4);
    expect(petals[0].fraction).toBeCloseTo(0.9473, 3);
    expect(Object.keys(fundMarkMissing({ ...NONE, ongoingChargePct: 0.2 }))).toHaveLength(4);
  });

  it("scores cheap, wide, long, fresh and complete as high", () => {
    const f = fundMarkFractions({
      ongoingChargePct: 0.1,
      holdingsCount: 500,
      top10Weight: 0.1,
      yearsOfHistory: 12,
      holdingsLagDays: 0,
      fieldsHave: 10,
      fieldsOf: 10,
    });
    expect(Object.values(f).every((v) => v === 1)).toBe(true);
  });

  it("falls back to the holdings count when the top-ten weight is unknown", () => {
    expect(fundMarkFractions({ ...NONE, holdingsCount: 50 }).spread).toBe(0.5);
  });

  it("scores a stale list and an expensive fund as low", () => {
    const f = fundMarkFractions({ ...NONE, holdingsLagDays: 120, ongoingChargePct: 2.5 });
    expect(f.freshness).toBe(0);
    expect(f.cost).toBe(0);
  });

  it("reads the ongoing charge from a profile", () => {
    expect(ongoingChargeFrom({ ongoing_charge: "0.22" })).toBe(0.22);
    expect(ongoingChargeFrom({ other: 1 })).toBeNull();
    expect(ongoingChargeFrom(undefined)).toBeNull();
  });
});
