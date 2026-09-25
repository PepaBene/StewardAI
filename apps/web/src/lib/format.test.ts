import { describe, expect, it } from "vitest";

import {
  formatMoney,
  formatPercent,
  formatPp,
  formatPrice,
  formatQuantity,
  formatRelative,
  gainLossTone,
} from "./format";

const L = "en-US";

/** Intl separates the currency code with a no-break space; compare with a plain one. */
const n = (text: string) => text.replace(/\u00a0/g, " ");

describe("formatMoney", () => {
  it("formats with the currency code and two decimals", () => {
    expect(n(formatMoney(1234.5, "CZK", { locale: L }))).toBe("CZK 1,234.50");
  });

  it("formats minor-unit codes Intl does not know", () => {
    expect(n(formatMoney(4312.5, "GBX", { locale: L }))).toBe("GBX 4,312.50");
  });

  it("shows a sign when asked, never for zero", () => {
    expect(n(formatMoney(12, "CZK", { locale: L, signed: true }))).toBe("+CZK 12.00");
    expect(n(formatMoney(-12, "CZK", { locale: L, signed: true }))).toBe("-CZK 12.00");
    expect(n(formatMoney(0, "CZK", { locale: L, signed: true }))).toBe("CZK 0.00");
  });

  it("renders missing values as a dash, not zero", () => {
    expect(n(formatMoney(null, "CZK", { locale: L }))).toBe("—");
    expect(n(formatMoney(Number.NaN, "CZK", { locale: L }))).toBe("—");
  });
});

describe("other formatters", () => {
  it("keeps up to 4 decimals on prices", () => {
    expect(n(formatPrice(0.12345, "USD", L))).toBe("USD 0.1235");
    expect(n(formatPrice(228.37, "USD", L))).toBe("USD 228.37");
  });

  it("trims fractional share quantities", () => {
    expect(formatQuantity(3.5, L)).toBe("3.5");
    expect(formatQuantity(0.123456789, L)).toBe("0.123457");
  });

  it("formats fractions as percentages", () => {
    expect(formatPercent(0.1234, { locale: L })).toBe("12.3%");
    expect(formatPercent(0.05, { locale: L, signed: true })).toBe("+5.0%");
  });

  it("formats drift in percentage points", () => {
    expect(formatPp(0.012, L)).toBe("+1.2 pp");
    expect(formatPp(-0.1, L)).toBe("-10.0 pp");
    expect(formatPp(0, L)).toBe("0.0 pp");
    expect(formatPp(null, L)).toBe("—");
  });

  it("describes elapsed time", () => {
    const now = new Date("2026-09-25T20:40:00Z");
    expect(formatRelative(new Date("2026-09-25T20:39:30Z"), now)).toBe("just now");
    expect(formatRelative(new Date("2026-09-25T20:36:00Z"), now)).toBe("4 min ago");
    expect(formatRelative(new Date("2026-09-25T17:40:00Z"), now)).toBe("3 h ago");
    expect(formatRelative(new Date("2026-09-23T20:40:00Z"), now)).toBe("2 days ago");
  });

  it("maps P/L signs to gain/loss tones", () => {
    expect(gainLossTone(5)).toBe("gain");
    expect(gainLossTone(-5)).toBe("loss");
    expect(gainLossTone(0)).toBe("flat");
    expect(gainLossTone(null)).toBe("flat");
  });
});
