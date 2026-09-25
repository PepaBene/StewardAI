import { describe, expect, it } from "vitest";

import type { HoldingProfilesConfig } from "@/lib/api/client";

import { newProfile, profilesFromBody, profilesToBody } from "./profiles-form";

const BODY: HoldingProfilesConfig = {
  schema_version: 1,
  profiles: [
    {
      ticker: "AAPL_US_EQ",
      company_name: "Apple Inc.",
      aliases: ["Apple"],
      ir_url: "https://investor.apple.com/",
      results_urls: [],
      financial_calendar_url: null,
      regulatory_news_urls: [],
      extra_domains: ["globenewswire.com"],
      sec_cik: "320193",
      sec_ticker: null,
      fiscal_year_end_month: 9,
      reporting_currency: "USD",
      competitors: ["Samsung", "Alphabet"],
      suppliers: ["TSMC"],
      customers: [],
      watch_topics: ["App Store regulation"],
      exposures: [{ kind: "revenue_region", name: "Greater China", share: 0.16 }],
    },
  ],
};

describe("holding profiles form", () => {
  it("round-trips the API body", () => {
    expect(profilesToBody(profilesFromBody(BODY))).toEqual(BODY);
  });

  it("normalises typed values", () => {
    const form = newProfile("ASMLa_EQ");
    form.reporting_currency = " eur ";
    form.extra_domains = "GlobeNewswire.com\n\n";
    form.sec_ticker = "asml";
    const [profile] = profilesToBody([form]).profiles!;
    expect(profile.reporting_currency).toBe("EUR");
    expect(profile.extra_domains).toEqual(["globenewswire.com"]);
    expect(profile.sec_ticker).toBe("ASML");
    expect(profile.ir_url).toBeNull();
    expect(profile.fiscal_year_end_month).toBe(12);
  });
});
