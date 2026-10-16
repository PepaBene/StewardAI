import { describe, expect, it } from "vitest";

import {
  companyHref,
  formatFigure,
  formatRatio,
  fractionToPercent,
  gridMatrix,
  headlineSeries,
  percentileLabel,
  percentToFraction,
  rangePosition,
  sortScenarios,
  splitReason,
  toNumber,
} from "./company";

describe("headlineSeries", () => {
  const cell = (value: number | null) => ({ value, unit: "money" }) as never;
  const view = {
    columns: [
      { id: "a:2024", label: "FY 2024" },
      { id: "a:2025", label: "FY 2025" },
    ],
    statements: [
      {
        lines: [
          { key: "revenue", label: "Revenue", cells: { "a:2024": cell(100), "a:2025": cell(120) } },
          { key: "net_income", label: "Net income", cells: { "a:2025": cell(30) } },
          { key: "capex", label: "Capital expenditure", cells: { "a:2024": cell(null), "a:2025": cell(null) } },
        ],
      },
    ],
  } as never;

  it("keeps the asked order, leaves gaps null and gives the latest margin", () => {
    const out = headlineSeries(view, ["net_income", "revenue", "capex", "free_cash_flow"]);
    expect(out.map((s) => s.key)).toEqual(["net_income", "revenue"]);
    expect(out[0].points).toEqual([
      { label: "FY 2024", value: null },
      { label: "FY 2025", value: 30 },
    ]);
    expect(out[0].margin).toBeCloseTo(0.25);
    expect(out[1].margin).toBeNull();
  });
});

describe("percentileLabel", () => {
  it("uses the right ordinal suffix", () => {
    const label = (p: number) => percentileLabel(String(p / 100));
    expect(label(1)).toBe("1st percentile");
    expect(label(2)).toBe("2nd percentile");
    expect(label(3)).toBe("3rd percentile");
    expect(label(11)).toBe("11th percentile");
    expect(label(21)).toBe("21st percentile");
    expect(label(50)).toBe("50th percentile");
    expect(percentileLabel(null)).toBeNull();
  });
});

describe("gridMatrix", () => {
  it("leaves out a cell whose shift is unknown instead of placing it at zero", () => {
    const grid = gridMatrix([
      { growth_shift: "0.01", rate_shift: "0", value: "10" },
      { growth_shift: null, rate_shift: "0", value: "99" },
    ]);
    expect(grid.rowShifts).toEqual([0.01]);
    expect(grid.at(0, 0)).toBe(10);
  });
});

describe("company helpers", () => {
  it("treats missing and unparsable decimals as unknown, never zero", () => {
    expect(toNumber(null)).toBeNull();
    expect(toNumber("")).toBeNull();
    expect(toNumber("abc")).toBeNull();
    expect(toNumber("0")).toBe(0);
    expect(toNumber("12.5")).toBe(12.5);
    expect(formatFigure(null, "money")).toBeNull();
    expect(formatRatio(undefined, "fraction")).toBeNull();
  });

  it("formats figures and ratios in their unit", () => {
    expect(formatFigure("1234567", "money")).toBe("1,234,567");
    expect(formatFigure("0.125", "fraction")).toBe("12.5%");
    expect(formatFigure("3.456", "per_share")).toBe("3.46");
    expect(formatRatio("0.1234", "fraction")).toBe("12.3%");
    expect(formatRatio("12.345", "multiple")).toBe("12.35×");
    expect(formatRatio("41.6", "days")).toBe("42 d");
  });

  it("separates not-meaningful from missing", () => {
    expect(splitReason("n/m: non-positive net income")).toEqual({ kind: "nm", text: "non-positive net income" });
    expect(splitReason("missing cash")).toEqual({ kind: "missing", text: "missing cash" });
    expect(splitReason(null).kind).toBe("none");
  });

  it("builds the sensitivity matrix by growth and rate shift", () => {
    const m = gridMatrix([
      { growth_shift: "-0.02", rate_shift: "-0.01", value: "10" },
      { growth_shift: "-0.02", rate_shift: "0", value: "9" },
      { growth_shift: "0", rate_shift: "-0.01", value: "12" },
      { growth_shift: "0", rate_shift: "0", value: null },
    ]);
    expect(m.rowShifts).toEqual([-0.02, 0]);
    expect(m.colShifts).toEqual([-0.01, 0]);
    expect(m.at(0, 1)).toBe(9);
    expect(m.at(1, 1)).toBeNull();
  });

  it("positions a value on a range and refuses a degenerate one", () => {
    expect(rangePosition(15, 10, 20)).toBe(0.5);
    expect(rangePosition(25, 10, 20)).toBe(1);
    expect(rangePosition(5, 10, 10)).toBeNull();
    expect(rangePosition(null, 1, 2)).toBeNull();
  });

  it("converts assumptions between percent text and fractions", () => {
    expect(percentToFraction("5")).toBe(0.05);
    expect(percentToFraction("4,5%")).toBe(0.045);
    expect(percentToFraction("")).toBeNull();
    expect(fractionToPercent("0.0925")).toBe("9.25");
  });

  it("orders scenarios bear, base, bull and builds links", () => {
    expect(sortScenarios([{ name: "bull" }, { name: "x" }, { name: "bear" }, { name: "base" }]).map((s) => s.name)).toEqual(["bear", "base", "bull", "x"]);
    expect(companyHref("BRK.B_US_EQ", "ratios")).toBe("/companies/BRK.B_US_EQ/ratios");
    expect(companyHref("AAA")).toBe("/companies/AAA");
  });
});
