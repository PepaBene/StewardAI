import { describe, expect, it } from "vitest";

import { alertsByCompany, cellStatus, coverageMatrix, coveredShare } from "./summary";

const NOW = Date.parse("2026-03-10T12:00:00Z");

describe("filings summary", () => {
  it("ranks the last week's alerts by company", () => {
    const { rows, total } = alertsByCompany(
      [
        { ticker: "A", known_at: "2026-03-09T00:00:00Z" },
        { ticker: "B", known_at: "2026-03-08T00:00:00Z" },
        { ticker: "B", known_at: "2026-03-07T00:00:00Z" },
        { ticker: "C", known_at: "2026-02-01T00:00:00Z" },
      ],
      NOW,
    );
    expect(total).toBe(3);
    expect(rows).toEqual([
      { ticker: "B", count: 2 },
      { ticker: "A", count: 1 },
    ]);
  });

  it("classes a cell as covered, partial, missing or none", () => {
    expect(cellStatus({ tickers: 3, covered: 3 })).toBe("covered");
    expect(cellStatus({ tickers: 3, covered: 1 })).toBe("partial");
    expect(cellStatus({ tickers: 3, covered: 0 })).toBe("missing");
    expect(cellStatus({ tickers: 0, covered: 0 })).toBe("none");
    expect(cellStatus(undefined)).toBe("none");
  });

  it("builds a matrix with a cell per market and kind", () => {
    const m = coverageMatrix([
      { dataset: "filings", label: "Filings", jurisdiction: "US", tickers: 2, covered: 2, stale: 0 },
      { dataset: "insiders", label: "Insiders", jurisdiction: "US", tickers: 2, covered: 1, stale: 0 },
      { dataset: "filings", label: "Filings", jurisdiction: "DE", tickers: 1, covered: 0, stale: 0 },
    ]);
    expect(m.columns.map((c) => c.dataset)).toEqual(["filings", "insiders"]);
    expect(m.rows.map((r) => r.jurisdiction)).toEqual(["DE", "US"]);
    expect(m.rows[0].cells.map((c) => c.status)).toEqual(["missing", "none"]);
    expect(m.rows[1].cells.map((c) => c.text)).toEqual(["2 of 2", "1 of 2"]);
  });

  it("states no share when nothing is held", () => {
    expect(coveredShare([])).toBeNull();
    expect(coveredShare([{ dataset: "a", label: "A", jurisdiction: "US", tickers: 4, covered: 3, stale: 0 }])).toBe(0.75);
  });
});
