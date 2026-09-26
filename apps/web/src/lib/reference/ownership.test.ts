import { describe, expect, it } from "vitest";

import type { HoldingOwnership, ManagerChange } from "@/lib/api/client";

import {
  edgarFilingUrl,
  formatChange,
  formatShares,
  formatUsdCompact,
  managerChangeText,
  OWNERSHIP_NOTE,
  ownershipState,
  periodLabel,
  sortManagerChanges,
} from "./ownership";

const period = (p: string, over: Partial<HoldingOwnership["periods"][number]> = {}) => ({
  period: p,
  holders: 100,
  shares: "1000000",
  value_usd: "250000000",
  latest_filed: "2026-08-14",
  holders_change: 3,
  shares_change: "-2500",
  new_holders: 5,
  exited_holders: 2,
  ...over,
});

describe("13F ownership", () => {
  it("formats shares, dollar values and changes (numbers may come as strings)", () => {
    expect(formatShares("1234567", "en-US")).toBe("1,234,567");
    expect(formatUsdCompact("1500000000", "en-US")).toBe("USD 1.5 bn");
    expect(formatUsdCompact(2_340_000, "en-US")).toBe("USD 2.3 m");
    expect(formatUsdCompact(null)).toBe("—");
    expect(formatChange(3, "en-US")).toEqual({ text: "+3", words: "up 3" });
    expect(formatChange("-2500", "en-US")).toEqual({ text: "-2,500", words: "down 2500" });
    expect(formatChange(null).words).toMatch(/no earlier period/);
  });

  it("dates each period by its quarter end and filing date", () => {
    const d = (s: string) => s;
    expect(periodLabel({ period: "2026-06-30", latest_filed: "2026-08-14" }, d)).toBe("As of 2026-06-30 · filed up to 2026-08-14");
    expect(periodLabel({ period: "2026-06-30", latest_filed: null }, d)).toMatch(/filing date unknown/);
    expect(OWNERSHIP_NOTE).toBe("SEC Form 13F; managers above USD 100 M; filed up to 45 days after quarter end.");
  });

  it("shows an empty state for a holding no 13F filing lists (non-US)", () => {
    const empty: HoldingOwnership = { ticker: "CEZ_CZ_EQ", cusips: [], periods: [], top_holders: [], source: "SEC Form 13F data sets" };
    const state = ownershipState(empty);
    expect(state.kind).toBe("empty");
    expect(state.kind === "empty" && state.reason).toMatch(/US-listed securities only/);
  });

  it("orders periods newest first", () => {
    const data: HoldingOwnership = {
      ticker: "AAPL_US_EQ",
      cusips: ["037833100"],
      periods: [period("2026-03-31"), period("2026-06-30")],
      top_holders: [],
      source: "SEC Form 13F data sets",
    };
    const state = ownershipState(data);
    expect(state.kind === "ok" && state.periods.map((p) => p.period)).toEqual(["2026-06-30", "2026-03-31"]);
  });
});

describe("tracked managers in a digest", () => {
  const change = (over: Partial<ManagerChange>): ManagerChange => ({
    cik: "1067983",
    manager_name: "Berkshire Hathaway Inc",
    ticker: "AAPL_US_EQ",
    change: "new",
    period: "2026-06-30",
    previous_period: "2026-03-31",
    shares: 1200,
    accession: "0000950123-26-008123",
    ...over,
  });

  it("lists new positions before exits and words each change", () => {
    const sorted = sortManagerChanges([change({ change: "exited", ticker: "MSFT_US_EQ" }), change({})]);
    expect(sorted.map((c) => c.change)).toEqual(["new", "exited"]);
    expect(managerChangeText(change({}), "en-US")).toBe("Opened a position of 1,200 shares");
    expect(managerChangeText(change({ change: "exited", shares: "5000" }), "en-US")).toBe("Exited the position (held 5,000 shares)");
    expect(managerChangeText(change({ shares: null }))).toBe("Opened a position");
  });

  it("links the filing on EDGAR", () => {
    expect(edgarFilingUrl("0001067983", "0000950123-26-008123")).toBe(
      "https://www.sec.gov/Archives/edgar/data/1067983/000095012326008123/0000950123-26-008123-index.htm",
    );
    expect(edgarFilingUrl("abc", "x")).toBeNull();
  });
});
