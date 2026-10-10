import { describe, expect, it } from "vitest";

import { EVENT_TYPES } from "@/lib/news/labels";

import {
  DEFAULT_ANNOUNCEMENT_RULES,
  DEFAULT_MACRO_SURPRISE_BANDS,
  DEFAULT_NOTIFICATIONS,
  DEFAULT_SOURCES,
  newAnnouncementRule,
  eventRulesFromBody,
  eventRulesToBody,
  newRule,
  formatPortfolioTopics,
  notificationsFromBody,
  notificationsToBody,
  parseDomainTiers,
  parsePortfolioTopics,
  parseSurpriseBands,
  PER_HOLDING,
  sourcesFromBody,
  sourcesToBody,
} from "./news-forms";

describe("notifications form", () => {
  it("round-trips the defaults unchanged", () => {
    expect(notificationsToBody(notificationsFromBody(null))).toEqual(DEFAULT_NOTIFICATIONS);
  });

  it("changing a rule's channel changes the saved body", () => {
    const form = notificationsFromBody(null);
    form.rules[0].channels = ["email", "telegram"];
    form.daily_alert_cap = "";
    form.rules.push({ ...newRule(), id: "apple", tickers: "AAPL_US_EQ\n\n", severities: [2, 3] });
    const body = notificationsToBody(form);
    expect(body.rules![0].channels).toEqual(["telegram", "email"]);
    expect(body.daily_alert_cap).toBeNull();
    expect(body.rules![1].match).toEqual({ severities: [2, 3], event_types: [], tickers: ["AAPL_US_EQ"] });
  });

  it("carries quiet days, the holiday country and the monitoring alert severity", () => {
    const form = notificationsFromBody(null);
    expect(form.quiet_all_day_on).toEqual([]);
    expect(form.holiday_country).toBe("CZ");
    expect(form.system_alert_severity).toBe(3);
    form.quiet_all_day_on = ["holidays", "weekends"];
    form.holiday_country = " de ";
    form.system_alert_severity = 0;
    const body = notificationsToBody(form);
    expect(body.quiet_all_day_on).toEqual(["weekends", "holidays"]);
    expect(body.holiday_country).toBe("DE");
    expect(body.system_alert_severity).toBe(0);
    expect(notificationsToBody(notificationsFromBody(body))).toEqual(body);
  });

  it("leaves a half-typed number for the backend to reject", () => {
    const form = notificationsFromBody(null);
    form.repeat_throttle_hours = "2x";
    expect(notificationsToBody(form).repeat_throttle_hours).toBe("2x");
  });
});

describe("news sources form", () => {
  const defaults = {
    schema_version: 1 as const,
    sources: [
      { id: "edgar-filings", name: "EDGAR", tier: 1 as const, type: "edgar" as const, url: null, enabled: true, tickers: [], macro: false, paywall: "open" as const, state_media: false, needs_alt: false },
      { id: "ecb-press", name: "ECB", tier: 1 as const, type: "rss" as const, url: "https://www.ecb.europa.eu/rss/press.html", enabled: true, tickers: [], macro: true, paywall: "open" as const, state_media: false, needs_alt: false },
    ],
    domain_tiers: [{ domain: "reuters.com", tier: 2 as const }],
    blocked_domains: [],
    allowed_domains: ["reuters.com"],
    portfolio_topics: [],
    unknown_outlet_tier: 3 as const,
    freshness_hours: 72,
    include_tier3: false,
    google_news_locale: { language: "en-US", country: "US" },
  };

  it("round-trips", () => {
    expect(sourcesToBody(sourcesFromBody(null, defaults))).toEqual(defaults);
  });

  it("keeps the fields the form has no input for (region, language, paywall, fetch policy)", () => {
    const seeded = {
      ...defaults,
      sources: [
        { ...defaults.sources[1], region: "DE", language: "de", paywall: "hard" as const, state_media: true, fetch: { rungs: [], display: "link_only" as const } },
      ],
    };
    const form = sourcesFromBody(seeded as never, defaults);
    form.sources[0].name = "Renamed";
    const out = sourcesToBody(form).sources![0];
    expect(out).toMatchObject({ name: "Renamed", region: "DE", language: "de", paywall: "hard", state_media: true });
    expect(out.fetch).toEqual({ rungs: [], display: "link_only" });
  });

  it("keeps the macro flag and the aggregator allow list", () => {
    const form = sourcesFromBody(null, defaults);
    expect(form.sources.map((s) => s.macro)).toEqual([false, true]);
    expect(form.allowed_domains).toBe("reuters.com");
    form.sources[0].macro = true;
    form.allowed_domains = "Reuters.com\n\nft.com ";
    const body = sourcesToBody(form);
    expect(body.sources!.map((s) => s.macro)).toEqual([true, true]);
    expect(body.allowed_domains).toEqual(["reuters.com", "ft.com"]);
  });

  it("builds regulatory and full-text sources per holding, with no URL", () => {
    expect(PER_HOLDING).toEqual(expect.arrayContaining(["rns", "eqs", "edgar_fts"]));
    const form = sourcesFromBody(null, DEFAULT_SOURCES);
    const rns = form.sources.find((s) => s.type === "rns")!;
    rns.url = "https://www.investegate.co.uk";
    expect(sourcesToBody(form).sources!.find((s) => s.type === "rns")!.url).toBeNull();
    expect(sourcesToBody(sourcesFromBody(null, DEFAULT_SOURCES))).toEqual(DEFAULT_SOURCES);
    expect(DEFAULT_SOURCES.sources!.filter((s) => s.macro).map((s) => s.id)).toEqual([
      "fed-press",
      "ecb-press",
      "cnb-press",
      "boj-whatsnew",
      "boe-news",
      "bundesbank-press",
      "boc-press",
    ]);
  });

  it("edits portfolio watch topics one per line and keeps a disabled topic disabled", () => {
    const form = sourcesFromBody(null, DEFAULT_SOURCES);
    expect(form.portfolio_topics.split("\n")[0]).toBe("rates | Interest rates | rates | interest rate, rate cut, rate hike, central bank, yield curve, inflation");
    form.portfolio_topics = "off Rates | Rates | rates | interest rate, cut\nfx | FX | currency |  yen ,euro ";
    expect(sourcesToBody(form).portfolio_topics).toEqual([
      { id: "rates", label: "Rates", kind: "rates", keywords: ["interest rate", "cut"], enabled: false },
      { id: "fx", label: "FX", kind: "currency", keywords: ["yen", "euro"], enabled: true },
    ]);
    expect(parsePortfolioTopics(formatPortfolioTopics(DEFAULT_SOURCES.portfolio_topics!))).toEqual(DEFAULT_SOURCES.portfolio_topics);
  });

  it("covers the official disclosure sources of each market, per holding with no URL", () => {
    expect(PER_HOLDING).toEqual(expect.arrayContaining(["tdnet", "hkex", "cninfo", "dart", "gazette", "filings"]));
    const form = sourcesFromBody(null, DEFAULT_SOURCES);
    const tdnet = form.sources.find((s) => s.type === "tdnet")!;
    tdnet.url = "https://example.com";
    expect(sourcesToBody(form).sources!.find((s) => s.type === "tdnet")!.url).toBeNull();
    const services = DEFAULT_ANNOUNCEMENT_RULES.map((r) => r.service);
    for (const s of ["tdnet", "hkex", "cninfo", "dart", "gazette", "filings"]) expect(services).toContain(s);
    expect(services.at(-1)).toBe("any");
  });

  it("drops the URL of per-holding sources and parses domain tiers", () => {
    const form = sourcesFromBody(null, defaults);
    form.sources[0].url = "https://example.com";
    expect(sourcesToBody(form).sources![0].url).toBeNull();
    expect(parseDomainTiers("Reuters.com 2\ncnbc.com\n")).toEqual([
      { domain: "reuters.com", tier: 2 },
      { domain: "cnbc.com", tier: "" },
    ]);
  });
});

describe("event rules form", () => {
  it("fills every event type and keeps overrides", () => {
    const form = eventRulesFromBody({ schema_version: 1, severities: { other: 1 }, overrides: [{ ticker: "AAPL_US_EQ", event_type: "product_business", severity: 3 }] } as never);
    const body = eventRulesToBody(form);
    expect(Object.keys(body.severities!)).toHaveLength(EVENT_TYPES.length);
    expect(body.severities!.other).toBe(1);
    expect(body.severities!.m_and_a).toBe(3);
    expect(body.overrides).toEqual([{ ticker: "AAPL_US_EQ", event_type: "product_business", severity: 3 }]);
  });
});

describe("event rules form: price moves, macro surprises and announcement types", () => {
  it("starts from the backend defaults", () => {
    const form = eventRulesFromBody(null);
    expect(form.price_move_threshold_pct).toBe("8");
    const body = eventRulesToBody(form);
    expect(body.price_move_threshold_pct).toBe(8);
    expect(body.macro_surprise_bands).toEqual(DEFAULT_MACRO_SURPRISE_BANDS);
    expect(body.announcement_types).toEqual(DEFAULT_ANNOUNCEMENT_RULES);
    expect(body.announcement_types!.at(-1)).toEqual({ service: "any", pattern: ".", event_type: "classify" });
    // Regular expressions keep their backslashes.
    expect(body.announcement_types!.some((r) => r.pattern.includes(String.raw`rule 2\.[47]`))).toBe(true);
  });

  it("round-trips a saved body and parses edits", () => {
    const saved = eventRulesToBody(eventRulesFromBody(null));
    const form = eventRulesFromBody({
      ...saved,
      price_move_threshold_pct: 6.5,
      macro_surprise_bands: { hicp: 0.2, fed_funds: 0 },
      announcement_types: [{ service: "eqs", pattern: "ad-?hoc", event_type: "classify" }],
    });
    expect(form.macro_surprise_bands).toBe("hicp 0.2\nfed_funds 0");
    form.price_move_threshold_pct = "7,5";
    form.macro_surprise_bands = "HICP 0,25\n\nus_cpi 0.4\ngdp x";
    form.announcement_types.unshift({ ...newAnnouncementRule(), service: "rns", pattern: "final results  ", event_type: "earnings_release" });
    const body = eventRulesToBody(form);
    expect(body.price_move_threshold_pct).toBe(7.5);
    expect(body.macro_surprise_bands).toEqual({ hicp: 0.25, us_cpi: 0.4, gdp: "x" });
    expect(body.announcement_types).toEqual([
      { service: "rns", pattern: "final results", event_type: "earnings_release" },
      { service: "eqs", pattern: "ad-?hoc", event_type: "classify" },
    ]);
  });

  it("parses surprise bands line by line", () => {
    expect(parseSurpriseBands("hicp 0.3\nfed_funds 0\n")).toEqual({ hicp: 0.3, fed_funds: 0 });
    expect(parseSurpriseBands("gdp")).toEqual({ gdp: "" });
  });
});

describe("news forms: Google News edition and earnings check cooldown", () => {
  it("edits the Google News edition and defaults an old body to en-US / US", () => {
    const form = sourcesFromBody({ ...DEFAULT_SOURCES, google_news_locale: undefined }, DEFAULT_SOURCES);
    expect([form.google_news_language, form.google_news_country]).toEqual(["en-US", "US"]);
    form.google_news_language = " cs ";
    form.google_news_country = "cz";
    expect(sourcesToBody(form).google_news_locale).toEqual({ language: "cs", country: "CZ" });
  });

  it("edits the earnings check cooldown, 10 days by default", () => {
    const form = eventRulesFromBody(null);
    expect(form.earnings_check_cooldown_days).toBe("10");
    form.earnings_check_cooldown_days = "5";
    expect(eventRulesToBody(form).earnings_check_cooldown_days).toBe(5);
  });
});
