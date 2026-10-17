import { describe, expect, it } from "vitest";

import { AUTO_LOGGED, journalVerdict, moveSince, priceText, sinceText } from "./journal";

const entry = (decision: string, price: number, currency = "USD", hypothesis = "Cloud growth.") => ({
  decision,
  ticker: "X",
  hypothesis,
  price_at_decision: price,
  price_currency: currency,
});

describe("journal verdicts", () => {
  it("prints a stored price without float noise", () => {
    expect(priceText("1587.59999999999990905052982270717620849609375", "EUR")).toBe("1,587.60 EUR");
    expect(priceText(null)).toBeNull();
  });

  it("reads a move the way the decision points", () => {
    const q = { price: 110, currency: "USD" };
    expect(moveSince(entry("buy", 100), q)).toBeCloseTo(0.1);
    expect(moveSince(entry("buy", 100, "EUR"), q)).toBeNull();
    expect(sinceText(entry("buy", 100), q)).toEqual({ tone: "good", text: "+10.0% since you bought (price only, before currency and dividends)." });
    expect(sinceText(entry("sell", 100), q)?.text).toBe("+10.0% since you sold: staying in would have earned this so far.");
    expect(sinceText(entry("pass", 120), q)?.tone).toBe("good");
  });

  it("says when the ledger has no reasons to judge", () => {
    const auto = `${AUTO_LOGGED} The reasons for this trade have not been written.`;
    const quotes = new Map([["X", { price: 110, currency: "USD" }]]);
    const v = journalVerdict([entry("buy", 100, "USD", auto), entry("buy", 120, "USD", auto)], quotes);
    expect(v?.tone).toBe("caution");
    expect(v?.text).toContain("1 of 2 buys are above their purchase price so far; all 2 decisions were logged");
    expect(journalVerdict([], quotes)).toBeNull();
  });
});
