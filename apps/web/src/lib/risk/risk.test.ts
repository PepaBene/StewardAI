import { describe, expect, it } from "vitest";

import { buildShock, EMPTY_SHOCK_FORM, ladderShare, normaliseSeparators, parseKeyed, parseLegs, parseNumber, parsePercent, sortChecks, type PretradeCheck } from "./risk";

describe("parsePercent", () => {
  it("reads a typed percentage as a fraction", () => {
    expect(parsePercent("-20")).toBeCloseTo(-0.2);
    expect(parsePercent("12,5 %")).toBeCloseTo(0.125);
    expect(parsePercent("−10")).toBeCloseTo(-0.1);
  });
  it("tells blank from junk", () => {
    expect(parsePercent("  ")).toBeNull();
    expect(parsePercent("abc")).toBeUndefined();
  });
});

describe("normaliseSeparators", () => {
  it("reads a comma as thousands only in groups of three", () => {
    expect(normaliseSeparators("1,500")).toBe("1500");
    expect(normaliseSeparators("1,000,000")).toBe("1000000");
    expect(normaliseSeparators("12,5")).toBe("12.5");
    expect(normaliseSeparators("1.234,5")).toBe("1234.5");
    expect(normaliseSeparators("1,234.5")).toBe("1234.5");
  });
  it("lets a trade amount and a basis-point rate keep their size", () => {
    expect(parseNumber("1,500")).toBe(1500);
    expect(parseNumber("1,000")).toBe(1000);
    expect(parseNumber("1,5")).toBe(1.5);
  });
});

describe("parseKeyed", () => {
  it("reads one move per line and upper-cases currency codes", () => {
    const got = parseKeyed("usd -10\nEUR: 5%\nGBP=2", { percent: true });
    expect(got.problems).toEqual([]);
    expect(got.values.USD).toBeCloseTo(-0.1);
    expect(got.values.EUR).toBeCloseTo(0.05);
    expect(got.values.GBP).toBeCloseTo(0.02);
  });
  it("keeps the case of sector names and reports bad lines", () => {
    const got = parseKeyed("Technology -15\nnonsense", { percent: true, keepCase: true });
    expect(got.values.Technology).toBeCloseTo(-0.15);
    expect(got.problems).toHaveLength(1);
  });
});

describe("buildShock", () => {
  it("asks for at least one move", () => {
    expect(buildShock(EMPTY_SHOCK_FORM)).toEqual({ error: "Enter at least one move to apply." });
  });
  it("builds fractions for percentages and keeps basis points as typed", () => {
    const built = buildShock({ ...EMPTY_SHOCK_FORM, equity: "-20", rate: "100", fx: "USD -10", durations: "IEAC 7.5" });
    if (!("body" in built)) throw new Error(built.error);
    expect(built.body.shocks.equity_pct).toBeCloseTo(-0.2);
    expect(built.body.shocks.rate_bp).toBe(100);
    expect(built.body.shocks.fx?.USD).toBeCloseTo(-0.1);
    expect(built.body.assumptions.duration_years).toEqual({ IEAC: 7.5 });
    expect(built.body.assumptions.unmeasured_equity_beta).toBe(1);
  });
  it("lets a blank beta leave unmeasured holdings out", () => {
    const built = buildShock({ ...EMPTY_SHOCK_FORM, equity: "-5", unmeasuredBeta: "" });
    if (!("body" in built)) throw new Error(built.error);
    expect(built.body.assumptions.unmeasured_equity_beta).toBeNull();
  });
  it("names the field that is wrong", () => {
    const built = buildShock({ ...EMPTY_SHOCK_FORM, equity: "lots" });
    expect(built).toEqual({ error: "The equity move must be a percentage such as -20." });
  });
});

describe("parseLegs", () => {
  it("reads buys and sells, keeping the ticker as typed (the server matches its case)", () => {
    expect(parseLegs("buy CSPXl_EQ 1500\nSELL CSPX 800,50")).toEqual({
      legs: [
        { side: "buy", ticker: "CSPXl_EQ", value_base: 1500 },
        { side: "sell", ticker: "CSPX", value_base: 800.5 },
      ],
    });
  });
  it("rejects a line it cannot read", () => {
    expect("error" in parseLegs("hold VUAA 10")).toBe(true);
    expect("error" in parseLegs("")).toBe(true);
    expect("error" in parseLegs("buy VUAA 0")).toBe(true);
  });
});

describe("ladderShare / sortChecks", () => {
  it("scales to the largest move and never exceeds one", () => {
    expect(ladderShare(-0.1, 0.2)).toBeCloseTo(0.5);
    expect(ladderShare(0.5, 0.2)).toBe(1);
    expect(ladderShare(0.1, 0)).toBe(0);
  });
  it("puts flags first and passes last", () => {
    const mk = (id: string, status: PretradeCheck["status"]): PretradeCheck => ({ id, principle: "", ticker: null, status, title: id, detail: "" });
    const order = sortChecks([mk("a", "pass"), mk("b", "info"), mk("c", "flag"), mk("d", "unavailable")]).map((c) => c.id);
    expect(order).toEqual(["c", "d", "b", "a"]);
  });
});
