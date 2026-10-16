import { describe, expect, it } from "vitest";
import { humanise, label, looksLikeKey } from ".";

describe("humanise", () => {
  it("strips Member and Segment suffixes and splits camel case", () => {
    expect(humanise("ProductLineMember")).toBe("Product Line");
    expect(humanise("EuropeanUnionSegment")).toBe("European Union");
  });
  it("splits snake and kebab case", () => {
    expect(humanise("us_10y")).toBe("Us 10y");
    expect(humanise("monthly-review")).toBe("Monthly review");
  });
  it("drops a namespace prefix", () => {
    expect(humanise("us-gaap:RetailMember")).toBe("Retail");
  });
  it("never returns an empty string", () => {
    expect(humanise("Member")).toBe("Member");
  });
});

describe("label", () => {
  it("names XBRL segment members", () => {
    expect(label("segment", "IPhoneMember")).toBe("iPhone");
    expect(label("segment", "ServiceMember")).toBe("Services");
    expect(label("segment", "SolarPanelsMember")).toBe("Solar Panels");
  });
  it("names macro series", () => {
    expect(label("macro", "commodity_wti")).toBe("Crude oil (WTI)");
    expect(label("macro", "fed_funds")).toBe("Fed funds rate");
    expect(label("macro", "commodity_cobalt")).toBe("Commodity cobalt");
  });
  it("turns strategy rule ids into plain labels", () => {
    expect(label("rule", "rule:max-country:geography:Unclassified")).toBe("Country limit: Unclassified");
    expect(label("rule", "min-cash:cash")).toBe("Minimum cash");
    expect(label("rule", "rule:max-tech:sector:")).toBe("Tech limit");
    expect(label("rule", "rule:listing-exact-match")).toBe("Exact listing match");
  });
  it("names jobs, known and unknown", () => {
    expect(label("job", "portfolio_sync")).toBe("Portfolio sync");
    expect(label("job", "inbox_agent")).toBe("Inbox agent");
    expect(label("job", "brand_new_job")).toBe("Brand new job");
  });
  it("names data providers, case-insensitively", () => {
    expect(label("provider", "twelvedata")).toBe("Twelve Data");
    expect(label("provider", "twelve_data")).toBe("Twelve Data");
    expect(label("provider", "FMP")).toBe("Financial Modeling Prep");
    expect(label("provider", "yahoo")).toBe("Yahoo Finance");
    expect(label("provider", "sec")).toBe("SEC EDGAR");
    expect(label("provider", "some_new_api")).toBe("Some new api");
  });
  it("does not trust inherited object keys", () => {
    expect(label("provider", "constructor")).toBe("Constructor");
  });
  it("never returns the raw key for an unknown one", () => {
    for (const kind of ["segment", "macro", "rule", "job", "provider"] as const) {
      expect(label(kind, "weird_key_name")).not.toBe("weird_key_name");
    }
  });
});

describe("looksLikeKey", () => {
  it("spots machine keys but not words", () => {
    expect(looksLikeKey("commodity_wti")).toBe(true);
    expect(looksLikeKey("IPhoneMember")).toBe(true);
    expect(looksLikeKey("Crude oil (WTI)")).toBe(false);
    expect(looksLikeKey("Services")).toBe(false);
  });
});
