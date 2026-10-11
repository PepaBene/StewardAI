import { describe, expect, it } from "vitest";

import { buildSpec, describeSpec, parseField } from "./shock";

describe("shock input", () => {
  it("reads typed numbers inside their range and accepts a decimal comma", () => {
    expect(parseField("-20", -95, 200)).toBe(-20);
    expect(parseField(" 1,5 ", -5, 5)).toBe(1.5);
    expect(parseField("", -5, 5)).toBeNull();
    expect(parseField("abc", -5, 5)).toBeNull();
    expect(parseField("9", -5, 5)).toBeNull();
    expect(parseField("1e3", -5000, 5000)).toBeNull();
  });

  it("turns percentages into fractions, leaves empty fields out and reports bad ones", () => {
    const { spec, problems } = buildSpec({ market: "-20", usd: "-10", rates_pp: "1", value: "x" }, "1.2", [
      { ticker: "AAPL", beta: "1.5" },
      { ticker: "", beta: "" },
    ]);
    expect(spec.market).toBeCloseTo(-0.2);
    expect(spec.usd).toBeCloseTo(-0.1);
    expect(spec.rates_pp).toBe(1);
    expect(spec.value).toBeUndefined();
    expect(spec.unmeasured_beta).toBe(1.2);
    expect(spec.owner_betas).toEqual({ AAPL: 1.5 });
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain("Value against growth");
  });

  it("leaves unmeasured holdings flat when the fallback beta is empty and flags a bad owner beta", () => {
    const flat = buildSpec({}, "", []);
    expect(flat.spec.unmeasured_beta).toBeNull();
    const bad = buildSpec({}, "1", [{ ticker: "X", beta: "9" }]);
    expect(bad.problems[0]).toContain("Own beta for X");
  });

  it("describes the moves of a scenario in plain words", () => {
    expect(describeSpec({ market: -0.2, usd: -0.1, rates_pp: 1, owner_betas: {}, unmeasured_beta: 1 })).toBe(
      "Stock market -20 %, US dollar -10 %, US 10-year yield +1 points",
    );
    expect(describeSpec({ owner_betas: {}, unmeasured_beta: 1 })).toBe("No moves set");
  });
});
