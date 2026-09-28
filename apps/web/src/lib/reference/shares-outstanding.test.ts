import { describe, expect, it } from "vitest";

import type { HoldingOwnership, SharesOutstandingOut } from "@/lib/api/client";

import { adsRatioText, denominatorText, formatOutstandingPct, holderPctByCik, outstandingView } from "./shares-outstanding";

const so = (over: Partial<SharesOutstandingOut> = {}): SharesOutstandingOut => ({
  shares: "25932524521",
  as_of: "2025-12-31",
  method: "dei_companyfacts",
  method_label: "cover page (dei:EntityCommonStockSharesOutstanding), SEC companyfacts",
  classes: [],
  denominator_label: "shares outstanding (single figure)",
  accession: "0001628280-26-025362",
  form: "20-F",
  filed: "2026-04-16",
  source_url: null,
  source_tier: "SEC EDGAR XBRL (official filing)",
  ads_ratio: 5,
  ads_ratio_source: "default",
  ads_ratio_reported: 5,
  ads_ratio_text: null,
  foreign_issuer: true,
  notes: [],
  ...over,
});

const ownership = (over: Partial<HoldingOwnership> = {}): HoldingOwnership => ({
  ticker: "TSM_US_EQ",
  cusips: ["874039100"],
  periods: [],
  top_holders: [
    { cik: "1", manager_name: "Big", shares: 5, value_usd: 1, accession: "0000000001-26-000001", pct_of_shares_outstanding: 0.0964 },
  ],
  source: "SEC Form 13F data sets",
  applicable: true,
  not_applicable_reason: null,
  shares_outstanding: so(),
  pct_of_shares_outstanding: 0.1157,
  basis_label: "US 13F filers' holdings of the US line — a floor",
  ...over,
});

describe("% of shares outstanding", () => {
  it("formats small fractions with more precision", () => {
    expect(formatOutstandingPct(0.0781)).toBe("7.8%");
    expect(formatOutstandingPct(0.00042)).toBe("0.04%");
    expect(formatOutstandingPct(null)).toBe("—");
  });

  it("words the ADS basis and the denominator", () => {
    expect(adsRatioText(so())).toBe("1 US share = 5 home-market shares");
    expect(adsRatioText(so({ ads_ratio: 1 }))).toBeNull();
    expect(denominatorText(so(), (d) => d)).toBe(
      "25.93B shares outstanding (shares outstanding (single figure)), as of 2025-12-31 — cover page (dei:EntityCommonStockSharesOutstanding), SEC companyfacts · 20-F",
    );
  });

  it("explains why a holding has no 13F figures", () => {
    const cez = ownership({ applicable: false, not_applicable_reason: "Not applicable: listed only outside the US (CZ)." });
    expect(outstandingView(cez)).toEqual({ kind: "not_applicable", reason: "Not applicable: listed only outside the US (CZ)." });
    expect(outstandingView(ownership({ shares_outstanding: null }))).toEqual({ kind: "no_denominator" });
    expect(outstandingView(ownership({ shares_outstanding: so({ method: "manual" }) }))).toEqual({ kind: "no_denominator" });
    const view = outstandingView(ownership());
    expect(view?.kind === "ok" && [view.pct, view.basis, view.ratio]).toEqual([
      0.1157,
      "US 13F filers' holdings of the US line — a floor",
      "1 US share = 5 home-market shares",
    ]);
    expect(outstandingView(null)).toBeNull();
  });

  it("maps holders to their share", () => {
    expect(holderPctByCik(ownership()).get("1")).toBe(0.0964);
    expect(holderPctByCik(null).size).toBe(0);
  });
});
