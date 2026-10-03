import { describe, expect, it } from "vitest";

import { formatAmount } from "./format";

describe("formatAmount", () => {
  it("keeps three decimals for a pure ratio (live: AAPL's 0.156 / 0.241 / 0.147 effective tax rate read 0.2 / 0.2 / 0.1)", () => {
    expect(formatAmount(0.156, "pure")).toBe("0.156");
    expect(formatAmount(0.241, "pure")).toBe("0.241");
    expect(formatAmount(0.147, "pure")).toBe("0.147");
  });
  it("leaves amounts and per-share values as before", () => {
    expect(formatAmount(416_160_000_000, "USD")).toBe("416.16B");
    expect(formatAmount(7.46, "iso4217:USD/shares")).toBe("7.46");
  });
});
