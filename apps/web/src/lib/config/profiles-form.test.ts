import { describe, expect, it } from "vitest";

import type { HoldingProfilesConfig } from "@/lib/api/client";

import { manualToBody, newProfile, profilesFromBody, profilesToBody } from "./profiles-form";

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
      price_symbol: "aapl.us",
      yahoo_symbol: null,
      reporting_currency: "USD",
      competitors: ["Samsung", "Alphabet"],
      suppliers: ["TSMC"],
      customers: [],
      watch_topics: ["App Store regulation"],
      exposures: [{ kind: "revenue_region", name: "Greater China", share: 0.16 }],
      valuation_source: "auto",
      manual_fundamentals: null,
    },
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
      reporting_currency: "CZK",
      competitors: [],
      suppliers: [],
      customers: [],
      watch_topics: [],
      exposures: [],
      valuation_source: "manual",
      yahoo_symbol: null,
      manual_fundamentals: {
        eps_ttm: 52.4,
        book_value_per_share: 449.9,
        dividend_per_share: null,
        currency: "CZK",
        as_of: "2025-12-31",
        source_note: "Annual Financial Report 2025",
      },
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
    form.price_symbol = " ASML.NL ";
    form.yahoo_symbol = " novo-b.co ";
    const [profile] = profilesToBody([form]).profiles!;
    expect(profile.reporting_currency).toBe("EUR");
    expect(profile.extra_domains).toEqual(["globenewswire.com"]);
    expect(profile.sec_ticker).toBe("ASML");
    expect(profile.price_symbol).toBe("asml.nl");
    expect(profile.yahoo_symbol).toBe("NOVO-B.CO");
    expect(profilesToBody([newProfile("X")]).profiles![0].price_symbol).toBeNull();
    expect(profile.ir_url).toBeNull();
    expect(profile.fiscal_year_end_month).toBe(12);
  });

  it("sends manual fundamentals only when something is entered", () => {
    const form = newProfile("KOMB_EQ");
    expect(profilesToBody([form]).profiles![0].manual_fundamentals).toBeNull();
    form.manual = { eps_ttm: "94,08", book_value_per_share: " ", dividend_per_share: "95.6", currency: "czk", as_of: "2026-06-30", source_note: " H1 report " };
    const [profile] = profilesToBody([form]).profiles!;
    expect(profile.manual_fundamentals).toEqual({
      eps_ttm: 94.08,
      book_value_per_share: null,
      dividend_per_share: 95.6,
      currency: "CZK",
      as_of: "2026-06-30",
      source_note: "H1 report",
    });
    expect(manualToBody({ eps_ttm: "", book_value_per_share: "", dividend_per_share: "", currency: "", as_of: "", source_note: "" })).toBeNull();
  });
});
