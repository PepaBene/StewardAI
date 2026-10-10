import { describe, expect, it } from "vitest";

import { checkFactValue, parseNavLines } from "./fund-research";

describe("checkFactValue", () => {
  it("upper-cases a currency and rejects a wrong length", () => {
    expect(checkFactValue("benchmark_currency", "usd")).toEqual({ value: "USD" });
    expect(checkFactValue("benchmark_currency", "US")).toHaveProperty("error");
  });

  it("accepts only non-negative numbers for maturity and duration", () => {
    expect(checkFactValue("effective_duration_years", "6.5")).toEqual({ value: "6.5" });
    expect(checkFactValue("average_maturity_years", "0")).toEqual({ value: "0" });
    expect(checkFactValue("average_maturity_years", "-1")).toHaveProperty("error");
    expect(checkFactValue("average_maturity_years", "about 5")).toHaveProperty("error");
  });

  it("accepts only price or total as the benchmark basis", () => {
    expect(checkFactValue("benchmark_basis", "total")).toEqual({ value: "total" });
    expect(checkFactValue("benchmark_basis", "unknown")).toHaveProperty("error");
  });
});

describe("parseNavLines", () => {
  it("reads date,value lines and reports the ones that do not fit", () => {
    const r = parseNavLines("2026-09-30,24.31\n\n2026-10-01 24.5\nnot a line\n2026-13-45,1");
    expect(r.rows).toEqual([
      { as_of: "2026-09-30", nav: "24.31" },
      { as_of: "2026-10-01", nav: "24.5" },
    ]);
    expect(r.bad).toEqual(["not a line", "2026-13-45,1"]);
  });
});
