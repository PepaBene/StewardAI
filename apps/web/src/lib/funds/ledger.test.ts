import { describe, expect, it } from "vitest";

import { flagText, rankRows, tierCounts, type LedgerRow } from "./ledger";

const row = (label: string, tier: LedgerRow["tier"]): LedgerRow => ({
  subject_kind: "fund",
  subject_key: label,
  label,
  tier,
  origin: "derived",
  proxy_fund_isin: null,
  total_return_available: null,
  historical_constituents: null,
  latest_holdings_as_of: null,
  reason: "x",
  source_url: null,
  checked_at: "2026-01-01T00:00:00Z",
} as LedgerRow);

describe("ledger", () => {
  it("ranks best source first and breaks ties by name", () => {
    const ranked = rankRows([row("B", "unavailable"), row("C", "official_holdings"), row("A", "official_holdings")]);
    expect(ranked.map((r) => r.label)).toEqual(["A", "C", "B"]);
  });

  it("keeps tiers with no entries at zero", () => {
    const counts = tierCounts([row("A", "proxy_fund")]);
    expect(counts).toHaveLength(5);
    expect(counts.find((c) => c.id === "proxy_fund")?.count).toBe(1);
    expect(counts.find((c) => c.id === "official_holdings")?.count).toBe(0);
  });

  it("never turns an unknown flag into no", () => {
    expect(flagText(null)).toBe("Unknown");
    expect(flagText(false)).toBe("No");
  });
});
