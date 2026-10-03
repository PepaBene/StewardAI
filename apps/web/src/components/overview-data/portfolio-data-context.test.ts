import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PortfolioDataContext } from "@/lib/overview-data/context";

import { PortfolioDataContextView } from "./portfolio-data-context";

const share = (n: number, d: number, basis = "total portfolio value") => ({ numerator: n, denominator: d, ratio: d > 0 ? n / d : null, denominator_basis: basis });

function ctx(over: Partial<PortfolioDataContext> = {}): PortfolioDataContext {
  return {
    snapshot_date: "2026-10-01", known_as_of: "2026-10-03T00:00:00Z", base_currency: "EUR", total_value: "1000", position_count: 2,
    identity_coverage: share(600, 1000), country_concentration: null, product_concentration: null, macro_sensitivities: [],
    adjusted_return_coverage: null, fund_overlap: null, fund_unknown: null, fund_total_value: null, gaps: [], ...over,
  };
}
const render = (context: Parameters<typeof PortfolioDataContextView>[0]["context"]) =>
  renderToStaticMarkup(createElement(PortfolioDataContextView, { context }));

describe("PortfolioDataContextView", () => {
  it("renders loading and error without data", () => {
    expect(render({ kind: "loading" })).toContain("Loading");
    const html = render({ kind: "error", title: "Backend unreachable", detail: "x", previous: null });
    expect(html).toContain("Backend unreachable");
    expect(html).toContain("unavailable".slice(0, 5));
  });
  it("renders the empty (no snapshot) state without zeros", () => {
    const html = render({ kind: "ok", data: ctx({ snapshot_date: null, position_count: 0, identity_coverage: null, gaps: [{ domain: "portfolio", state: "missing", reason: "No position snapshot exists." }] }) });
    expect(html).toContain("No position snapshot exists.");
    expect(html).not.toContain("0.0%");
  });
  it("renders partial data with the denominator beside percentages and keeps last-good on error", () => {
    const data = ctx({
      country_concentration: {
        dimension: "country", mapped: share(600, 1000), unmapped_value: 400, unmapped_tickers: ["B"], hhi: "0.5", hhi_denominator: 600, note: "",
        rows: [{ key: "US", exposed_value: 600, share_of_portfolio: share(600, 1000), share_of_mapped: share(600, 600, "mapped value"), by_basis: { reported: 600 }, holdings: 1 }],
      },
      adjusted_return_coverage: { note: "n", bases: [{ adjustment_basis: "total_return", complete: share(0, 1000), partial_value: 0, missing_value: 1000, complete_tickers: [], partial_tickers: [], missing_tickers: ["A", "B"] }] },
      gaps: [{ domain: "fund_overlap", state: "missing", reason: "No fund holdings snapshot was known." }],
    });
    const html = render({ kind: "error", title: "HTTP 500", detail: "boom", previous: data });
    expect(html).toContain("60.0% of");
    expect(html).toContain("total portfolio value");
    expect(html).toContain("Could not refresh");
    expect(html).toContain("No fund holdings snapshot was known.");
  });
});
