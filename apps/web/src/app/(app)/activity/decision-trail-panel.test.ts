import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TrailItem } from "./decision-trail-panel";

describe("TrailItem", () => {
  const base = { id: 1, created_at: "2026-10-01T10:00:00Z", rule: "trade_exposure", scope: "AAPL|8542", ticker: "AAPL", severity: 2, reason: "Within the throttle window.", news_item_id: null, job_id: 7 };
  it("explains a throttled decision with its evidence", () => {
    const html = renderToStaticMarkup(createElement(TrailItem, { row: { ...base, outcome: "throttled", evidence: { source_url: "https://x.org", trade_value: 5 } } }));
    expect(html).toContain("Throttled");
    expect(html).toContain("Within the throttle window.");
    expect(html).toContain("https://x.org");
  });
  it("renders a gate-blocked row and an empty evidence row", () => {
    const html = renderToStaticMarkup(createElement(TrailItem, { row: { ...base, job_id: null, outcome: "blocked_by_gate", evidence: { gate: { name: "g", allowed: false } } } }));
    expect(html).toContain("g: closed");
    expect(() => renderToStaticMarkup(createElement(TrailItem, { row: { ...base, job_id: null, outcome: "weird", evidence: {} } }))).not.toThrow();
  });
});
