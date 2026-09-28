import { describe, expect, it } from "vitest";

import type { HoldingProfilesConfig } from "@/lib/api/client";

import { fundamentalsFromProfile, fundamentalsToProfile, newFundamentals, newManualSegment, parseAmount } from "./fundamentals-form";
import { profilesFromBody, profilesToBody } from "./profiles-form";

const CEZ: HoldingProfilesConfig = {
  schema_version: 1,
  profiles: [
    {
      ticker: "CEZ_EQ",
      company_name: "ČEZ",
      aliases: [],
      ir_url: null,
      results_urls: [],
      financial_calendar_url: null,
      regulatory_news_urls: [],
      extra_domains: [],
      sec_cik: null,
      sec_ticker: null,
      fiscal_year_end_month: 12,
      price_symbol: null,
      yahoo_symbol: null,
      reporting_currency: "CZK",
      competitors: [],
      suppliers: [],
      customers: [],
      watch_topics: [],
      exposures: [],
      share_basis: {
        ads_ratio: null,
        share_classes: [],
        manual_shares_outstanding: 537989759,
        manual_as_of: "2025-12-31",
        manual_source: "CEZ shareholder structure page",
      },
      manual_segments: [
        {
          label: "Czech Republic",
          axis: "geographic",
          value: 250000000000,
          currency: "CZK",
          period_start: "2025-01-01",
          period_end: "2025-12-31",
          source: "Annual Report 2025",
        },
      ],
    },
  ],
};

describe("SEC XBRL profile fields", () => {
  it("round-trips a profile with a share basis and manual segments", () => {
    expect(profilesToBody(profilesFromBody(CEZ))).toEqual(CEZ);
  });

  it("sends nothing for untouched fields", () => {
    expect(fundamentalsToProfile(newFundamentals())).toEqual({});
    const defaults = fundamentalsFromProfile({
      share_basis: { ads_ratio: null, share_classes: [], manual_shares_outstanding: null, manual_as_of: null, manual_source: "" },
      manual_segments: [],
    });
    expect(fundamentalsToProfile(defaults)).toEqual({});
  });

  it("parses typed amounts and share classes", () => {
    const form = newFundamentals();
    form.ads_ratio = " 5 ";
    form.share_classes = "us-gaap:CommonClassAMember\n\n";
    const row = { ...newManualSegment(), label: " Czechia ", value: "250 000 000 000", currency: "czk" };
    form.segments = [row, newManualSegment(row)];
    const out = fundamentalsToProfile(form);
    expect(out.share_basis?.ads_ratio).toBe(5);
    expect(out.share_basis?.share_classes).toEqual(["us-gaap:CommonClassAMember"]);
    expect(out.manual_segments?.[0]).toMatchObject({ label: "Czechia", value: 250000000000, currency: "CZK" });
    // A new row copies currency and period from the previous one.
    expect(out.manual_segments?.[1]).toMatchObject({ currency: "CZK", label: "" });
    expect(parseAmount("1,5")).toBe(1.5);
    expect(parseAmount("")).toBeNull();
  });
});
