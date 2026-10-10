import { describe, expect, it } from "vitest";

import { buildEntry, daysUntil, EMPTY_FORM, evidenceLines, evidenceMeter, lines, reviewLabel, weightBar } from "./journal";

const filled = { ...EMPTY_FORM, ticker: "AAPL", hypothesis: "Services mix keeps margins up." };

describe("buildEntry", () => {
  it("sends trimmed text, one item per line, and nulls for blanks", () => {
    const built = buildEntry({ ...filled, catalysts: " Q4 print \n\n new product ", conviction: "4", main_reason: "quality", price: "187,5" });
    expect(built).toEqual({
      ok: true,
      body: {
        scope: "security",
        ticker: "AAPL",
        decision: "buy",
        parent_id: null,
        hypothesis: "Services mix keeps margins up.",
        valuation_assumptions: null,
        catalysts: ["Q4 print", "new product"],
        disconfirming: [],
        review_date: null,
        conviction: 4,
        main_reason: "quality",
        price_at_decision: 187.5,
      },
    });
  });

  it("asks for a security and for reasoning before anything is sent", () => {
    expect(buildEntry({ ...filled, ticker: " " })).toEqual({ ok: false, error: "Name the security this decision is about." });
    expect(buildEntry({ ...filled, hypothesis: "  " }).ok).toBe(false);
  });

  it("drops the ticker for a portfolio entry", () => {
    const built = buildEntry({ ...filled, scope: "portfolio" });
    expect(built.ok && built.body.ticker).toBeNull();
  });

  it("refuses a price that is not a positive number", () => {
    expect(buildEntry({ ...filled, price: "abc" }).ok).toBe(false);
    expect(buildEntry({ ...filled, price: "0" }).ok).toBe(false);
  });
});

describe("review dates", () => {
  const today = new Date(2026, 9, 8);
  it("counts days to a review date, negative once it has passed", () => {
    expect(daysUntil("2026-10-18", today)).toBe(10);
    expect(daysUntil("2026-10-05", today)).toBe(-3);
    expect(daysUntil(null, today)).toBeNull();
  });
  it("words the distance", () => {
    expect(reviewLabel(10)).toBe("review in 10 days");
    expect(reviewLabel(1)).toBe("review in 1 day");
    expect(reviewLabel(0)).toBe("review due today");
    expect(reviewLabel(-3)).toBe("review 3 days overdue");
    expect(reviewLabel(null)).toBeNull();
  });
});

describe("evidenceMeter", () => {
  it("fills squares up to the minimum and marks a smaller sample as thin", () => {
    expect(evidenceMeter(3, 5)).toEqual({ filled: 3, needed: 5, thin: true });
    expect(evidenceMeter(12, 5)).toEqual({ filled: 5, needed: 5, thin: false });
  });
});

describe("evidenceLines", () => {
  it("states the holding, price, thesis gaps and config in force", () => {
    const out = evidenceLines({
      holding: { weight: 0.123 },
      market: { last_close: 187.5, last_close_date: "2026-03-01", volatility_1y: 0.256 },
      thesis: { why_owned: "x" },
      thesis_gaps: ["a conviction level"],
      config_versions: { theses: 7, strategy: null, holding_profiles: 2 },
      method_versions: { risk: "1.1.0", dcf: "1.0.0" },
    });
    expect(out).toEqual([
      "Held at 12.3% of the portfolio.",
      "Last close 187.5 on 2026-03-01, one-year volatility 26%.",
      "The thesis still lacked: a conviction level.",
      "Config in force: theses #7, holding profiles #2.",
      "2 calculation methods, versions recorded.",
    ]);
  });
  it("says what was not there rather than skipping it", () => {
    expect(evidenceLines({ holding: null, thesis: null })).toEqual(["Not held at the time.", "No thesis was written for it."]);
  });
});

describe("weightBar and lines", () => {
  it("scales to the largest weight and shows nothing without one", () => {
    expect(weightBar(0.15, 0.3)).toBe(50);
    expect(weightBar(null, 0.3)).toBe(0);
    expect(weightBar(0.1, 0)).toBe(0);
  });
  it("splits text into trimmed non-blank lines", () => {
    expect(lines("a\n \n b ")).toEqual(["a", "b"]);
  });
});
