import { describe, expect, it } from "vitest";

import { formatCompactMoney, formatSignedPct } from "./money";

describe("compact money", () => {
  it("abbreviates with the currency code", () => {
    expect(formatCompactMoney(2_381_884, "CZK")).toBe("2.38M CZK");
    expect(formatCompactMoney(96_810.9, "CZK")).toBe("96.8k CZK");
    expect(formatCompactMoney(224_038, "CZK", { signed: true })).toBe("+224.0k CZK");
    expect(formatCompactMoney(-1_500, "CZK", { signed: true })).toBe("−1.5k CZK");
    expect(formatCompactMoney(null, "CZK")).toBe("—");
  });

  it("signs percentages with a true minus", () => {
    expect(formatSignedPct(0.087)).toBe("+8.7%");
    expect(formatSignedPct(-0.0026, 2)).toBe("−0.26%");
    expect(formatSignedPct(0)).toBe("0.0%");
    expect(formatSignedPct(null)).toBe("—");
  });
});
