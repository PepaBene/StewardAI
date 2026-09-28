import { describe, expect, it } from "vitest";

import { activeNavKey, CONFIG_NAV, navCounts } from "./sidebar";

describe("activeNavKey", () => {
  it("matches a page and its sub-pages", () => {
    expect(activeNavKey("/config/strategy")).toBe("strategy");
    expect(activeNavKey("/config/history/strategy")).toBe("history");
    expect(activeNavKey("/config")).toBeNull();
    expect(activeNavKey("/config/strategyx")).toBeNull();
  });

  it("lists every page once", () => {
    expect(new Set(CONFIG_NAV.map((i) => i.href)).size).toBe(CONFIG_NAV.length);
  });
});

describe("navCounts", () => {
  it("counts from the export's active bodies", () => {
    const counts = navCounts({
      domains: {
        strategy: { version_id: 3, created_at: "", note: null, body: { target_positions: [{}, {}] } },
        holding_profiles: { version_id: 6, created_at: "", note: null, body: { profiles: [{}, {}, {}] } },
        news_sources: { version_id: 1, created_at: "", note: null, body: { sources: [{ enabled: true }, { enabled: false }] } },
        jobs: { version_id: 1, created_at: "", note: null, body: { monitor: { cron: "*/15 * * * *", enabled: true }, news: { cron: "0 * * * *", enabled: false }, chat: { model: "x" } } },
        budget: { version_id: 1, created_at: "", note: null, body: { monthly_cap_usd: 40 } },
        prompts: { version_id: 1, created_at: "", note: null, body: { overrides: {} } },
        unknown_domain: { version_id: 1, created_at: "", note: null, body: {} },
      },
    } as never);
    expect(counts.strategy).toEqual({ text: "2", title: "2 position targets" });
    expect(counts["holding-profiles"]?.text).toBe("3");
    expect(counts["news-sources"]).toEqual({ text: "1", title: "1 of 2 sources enabled" });
    expect(counts.jobs?.text).toBe("1");
    expect(counts.budget?.text).toBe("$40");
    expect(counts.prompts?.text).toBe("built-in");
    expect(Object.keys(counts)).not.toContain("unknown_domain");
  });

  it("is empty without an export", () => {
    expect(navCounts(null)).toEqual({});
  });
});
