import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { buildExposureTreemap, buildLookThroughSankey } from "@/lib/xray/exposure";
import { buildRankFlow } from "@/lib/xray/ownership";
import type { AggregateReport, HoldingOwnership } from "@/lib/xray/types";

import { ConfirmAction } from "./confirm-action";
import { DataTable, Facts } from "./facts";
import { LookThroughSankeyChart } from "./lookthrough-sankey";
import { NestedTreemap } from "./nested-treemap";
import { RankFlowChart } from "./rank-flow";
import { SafeLink } from "./safe-link";
import { ResourceShell } from "./resource-shell";

const report = {
  as_of: "2026-10-03",
  portfolio_snapshot_date: "2026-10-02",
  base_currency: "EUR",
  total_value: 1000,
  available: true,
  method: "m",
  funds: [
    { isin: "IE00AAAAAAA1", label: "Fund A", ticker: "FNDA", weight: 0.5, value_base: 500, status: "ok", holdings_as_of: "2026-09-01", lag_days: 30, stale: false, source: "s", rows_total_weight: 0.97, residual_weight: 0.03, unknown_weight: 0.015, reconciles: false, expense_ratio: null, fee_drag: null, warnings: [] },
    { isin: "IE00BBBBBBB2", label: "Fund B", ticker: "FNDB", weight: 0.2, value_base: 200, status: "missing", holdings_as_of: null, lag_days: null, stale: false, source: null, rows_total_weight: null, residual_weight: null, unknown_weight: 0.2, reconciles: null, expense_ratio: null, fee_drag: null, warnings: ["no holdings snapshot stored yet"] },
  ],
  stale_funds: [],
  issuers: [
    { key: "i1", label: "Issuer One", merged_securities: 2, securities: [], weight: 0.2, value_base: 200, direct_weight: 0.05, fund_weight: 0.15, overlap_weight: 0.05, channels: [{ kind: "direct", ticker: "T", isin: null, label: "Direct", weight: 0.05, value_base: 50 }, { kind: "fund", ticker: "FNDA", isin: "IE00AAAAAAA1", label: "Fund A", weight: 0.15, value_base: 150 }], sector: "Tech", country: "US", region: "North America", currency: "USD" },
  ],
  overlap: [],
  exposures: [
    { dimension: "sector", reconciles: true, issuer_buckets: { i1: { Tech: 0.2 } }, buckets: [
      { key: "Tech", label: "Tech", kind: "classified", weight: 0.3, value_base: 300, direct_weight: 0.05, fund_weight: 0.25 },
      { key: "__cash__", label: "Cash", kind: "cash", weight: 0.1, value_base: 100, direct_weight: 0.1, fund_weight: 0 },
      { key: "__not_looked_through__", label: "Fund not looked through (unknown)", kind: "not_looked_through", weight: 0.2, value_base: 200, direct_weight: 0, fund_weight: 0.2 },
    ] },
  ],
  fees: null,
  unknown: null,
  notes: [],
} as unknown as AggregateReport;

describe("xray charts render", () => {
  it("nested treemap draws groups, the remainder tile and an accessible table", () => {
    const map = buildExposureTreemap(report, "sector")!;
    const html = renderToStaticMarkup(createElement(NestedTreemap, { map, label: "Exposure" }));
    expect(html).toContain("Issuer One");
    expect(html).toContain("Other issuers");
    expect(html).toContain("fund not looked through");
    expect(html).toContain("<table");
  });

  it("sankey keeps cash, the not-looked-through fund and flows in the table", () => {
    const sankey = buildLookThroughSankey(report, null)!;
    const html = renderToStaticMarkup(createElement(LookThroughSankeyChart, { sankey, label: "Flows" }));
    expect(html).toContain("Cash");
    expect(html).toContain("Fund not looked through");
    expect(html).toContain("Share of portfolio value");
  });

  it("rank flow renders new holders without a line and the table moves", () => {
    const own = {
      ticker: "X", cusips: [], source: "s", applicable: true,
      periods: [{ period: "2026-06-30", holders: 2, shares: 3, value_usd: 3, latest_filed: "2026-08-10", latest_filing_lag_days: 41 }],
      top_holders_previous_period: "2026-03-31",
      top_holders: [
        { cik: "1", manager_name: "Alpha", shares: 10, value_usd: 1, accession: "a", prev_shares: 5, filed: "2026-08-01", filing_lag_days: 32 },
        { cik: "2", manager_name: "Beta", shares: 8, value_usd: 1, accession: "b", prev_shares: null },
      ],
    } as unknown as HoldingOwnership;
    const flow = buildRankFlow(own)!;
    const html = renderToStaticMarkup(createElement(RankFlowChart, { flow, label: "Rank flow" }));
    expect(html).toContain("quarter end");
    expect(html).toContain("filed 41 d after quarter end");
    expect(html).toContain("no earlier position");
    expect(html).toContain("(+32 d)");
  });
});

describe("xray building blocks", () => {
  it("DataTable and Facts render with real headers", () => {
    const html = renderToStaticMarkup(
      createElement("div", null, createElement(Facts, { items: [{ label: "A", value: "1", note: "n" }] }), createElement(DataTable, { caption: "c", columns: [{ label: "X" }, { label: "Y", align: "right" }], rows: [["a", "b"]] })),
    );
    expect(html).toContain('scope="col"');
    expect(html).toContain('scope="row"');
  });

  it("ConfirmAction starts closed (no action on first render)", () => {
    const html = renderToStaticMarkup(createElement(ConfirmAction, { label: "Go", confirmTitle: "Sure?", confirmBody: "b", run: async () => "ok" }));
    expect(html).toContain("Go");
    expect(html).not.toContain("Sure?");
  });

  it("ResourceShell covers loading, error without data and error with last-good data", () => {
    const child = (d: string, last: boolean) => createElement("span", null, `${d}${last ? " (last good)" : ""}`);
    const loading = renderToStaticMarkup(ResourceShell<string>({ state: { kind: "loading" }, label: "Thing", children: child }));
    expect(loading).toContain("Loading Thing");
    const failed = renderToStaticMarkup(ResourceShell<string>({ state: { kind: "error", title: "Boom", detail: "d", previous: null }, label: "Thing", children: child }));
    expect(failed).toContain("Temporarily unavailable");
    expect(failed).toContain("Boom");
    const kept = renderToStaticMarkup(ResourceShell<string>({ state: { kind: "error", title: "Boom", detail: "d", previous: "kept" }, label: "Thing", children: child }));
    expect(kept).toContain("kept (last good)");
    expect(kept).toContain("Showing the last value that loaded");
  });
});

describe("F7-8 treemap focus, F7-10 safe links", () => {
  it("the nested treemap is one tab stop, not one per tile", () => {
    const map = buildExposureTreemap(report, "sector")!;
    const html = renderToStaticMarkup(createElement(NestedTreemap, { map, label: "Exposure" }));
    expect((html.match(/tabindex="0"/g) ?? []).length).toBe(1);
    expect(html).toContain("arrow keys");
  });
  it("a javascript: source URL is text, an https one is a link", () => {
    const bad = renderToStaticMarkup(createElement(SafeLink, { href: "javascript:alert(1)", className: "text-accent-ink" }, "filing"));
    expect(bad).not.toContain("<a");
    const ok = renderToStaticMarkup(createElement(SafeLink, { href: "https://www.sec.gov/x" }, "filing"));
    expect(ok).toContain('href="https://www.sec.gov/x"');
  });
});
