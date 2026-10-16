import { describe, expect, it } from "vitest";

import { groupMacro, macroGroup } from "./groups";

describe("macroGroup", () => {
  it.each([
    ["us_cpi", "Inflation"],
    ["fed_funds", "Rates"],
    ["us_10y", "Rates"],
    ["ecb_deposit_rate", "Rates"],
    ["euro_area_household_loan_rate", "Credit and money"],
    ["us_hy_spread", "Credit and money"],
    ["euro_area_m3_growth", "Credit and money"],
    ["us_unemployment", "Growth and labour"],
    ["commodity_copper", "Commodities"],
    ["eurusd", "Currencies"],
    ["something_new", "Other"],
  ])("%s -> %s", (id, group) => {
    expect(macroGroup(id)).toBe(group);
  });
});

describe("groupMacro", () => {
  it("keeps the fixed group order and drops empty groups", () => {
    const out = groupMacro(["commodity_nickel", "us_cpi", "fed_funds", "us_10y"], (s) => s);
    expect(out).toEqual([
      { group: "Rates", items: ["fed_funds", "us_10y"] },
      { group: "Inflation", items: ["us_cpi"] },
      { group: "Commodities", items: ["commodity_nickel"] },
    ]);
  });
});
