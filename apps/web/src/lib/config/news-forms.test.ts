import { describe, expect, it } from "vitest";

import { EVENT_TYPES } from "@/lib/news/labels";

import {
  DEFAULT_NOTIFICATIONS,
  eventRulesFromBody,
  eventRulesToBody,
  newRule,
  notificationsFromBody,
  notificationsToBody,
  parseDomainTiers,
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

  it("leaves a half-typed number for the backend to reject", () => {
    const form = notificationsFromBody(null);
    form.repeat_throttle_hours = "2x";
    expect(notificationsToBody(form).repeat_throttle_hours).toBe("2x");
  });
});

describe("news sources form", () => {
  const defaults = {
    schema_version: 1 as const,
    sources: [{ id: "edgar-filings", name: "EDGAR", tier: 1 as const, type: "edgar" as const, url: null, enabled: true, tickers: [] }],
    domain_tiers: [{ domain: "reuters.com", tier: 2 as const }],
    blocked_domains: [],
    unknown_outlet_tier: 3 as const,
    freshness_hours: 72,
    include_tier3: false,
  };

  it("round-trips", () => {
    expect(sourcesToBody(sourcesFromBody(null, defaults))).toEqual(defaults);
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
