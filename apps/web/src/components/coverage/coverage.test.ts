import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AtlasMatrix } from "@/components/coverage/atlas-matrix";
import { GlobalReach } from "@/components/coverage/global-reach";
import { IdentityReviewPanel, PriceCoveragePanel, ProvenancePanel, ReachPanel, reachRows } from "@/components/coverage/measured-panels";
import { PlanGaps } from "@/components/coverage/plan-gaps";
import { Scorecard } from "@/components/coverage/scorecard";
import { SummaryStrip } from "@/components/coverage/summary-strip";
import { LedgerTable } from "@/components/coverage/ledger-table";
import { buildAtlas, costScore, provenanceScore } from "@/lib/coverage/logic";
import type { CoverageEntry } from "@/lib/source-coverage/types";

const entry = (over: Partial<CoverageEntry>): CoverageEntry => ({
  id: "p:d:e",
  provider: "fred",
  dataset: "GDP",
  entityKey: "DE",
  entityKind: "geography",
  state: "paid_only",
  rawState: "paid_only",
  stale: null,
  staleAfterHours: null,
  staleBasis: null,
  staleReason: null,
  expectedPeriods: null,
  availablePeriods: null,
  coverageRatio: null,
  earliestPeriod: null,
  latestPeriod: null,
  ageDays: null,
  checkedAt: "2026-10-01T00:00:00Z",
  lastGoodObservationId: null,
  reason: "premium endpoint",
  ...over,
});

describe("coverage components render", () => {
  it("matrix: empty, populated and gap states without throwing", () => {
    expect(() => renderToStaticMarkup(createElement(AtlasMatrix, { rows: [], drill: null, onDrill: () => {} }))).not.toThrow();
    const html = renderToStaticMarkup(createElement(AtlasMatrix, { rows: buildAtlas([entry({})]), drill: null, onDrill: () => {} }));
    expect(html).toContain("Paid only");
    expect(html).toContain("No ledger entry");
  });
  it("ledger table shows reason and an empty message", () => {
    expect(renderToStaticMarkup(createElement(LedgerTable, { entries: [entry({})], caption: "c" }))).toContain("premium endpoint");
    expect(renderToStaticMarkup(createElement(LedgerTable, { entries: [], caption: "c" }))).toContain("unknown, not covered");
  });
  it("summary strip handles null keys and absent expected counts", () => {
    const html = renderToStaticMarkup(
      createElement(SummaryStrip, { data: { groupBy: "security", totalEntries: 1, note: "n", groups: [{ key: null, entries: 1, byState: { available: 1 }, expectedPeriods: null, availablePeriods: null, entriesWithExpected: 0, availableEntries: 1, coverageRatio: null }] } }),
    );
    expect(html).toContain("No expected count stated");
  });
  it("scorecard, global reach and plan gaps render; plan gaps is labelled as documentation", () => {
    expect(renderToStaticMarkup(createElement(Scorecard, { items: [provenanceScore(null)] }))).toContain("Unavailable");
    expect(renderToStaticMarkup(createElement(GlobalReach, { rows: [] }))).toContain("No observation rows");
    const gaps = renderToStaticMarkup(createElement(PlanGaps));
    expect(gaps).toContain("Plan scope, not data");
  });
});

describe("fix pass: coverage components", () => {
  it("F1-01: the scorecard never says Target met for a gate that audited nothing", () => {
    const gate = { passed: true, checked: 0, freeAvailable: 0, disabledOk: 0, gatedUnverified: 0, violationCount: 0, warningCount: 0, warningsByCode: {}, findings: [] };
    const html = renderToStaticMarkup(createElement(Scorecard, { items: [costScore(gate)] }));
    expect(html).not.toContain("Target met");
    expect(html).toContain("Unavailable");
    expect(html).toContain("manifest is empty");
  });
  it("F1-06: the ledger table says when rows are left out", () => {
    const html = renderToStaticMarkup(createElement(LedgerTable, { entries: [entry({})], totalMatching: 1200, caption: "c" }));
    expect(html).toContain("Showing 1 of 1200");
    expect(renderToStaticMarkup(createElement(LedgerTable, { entries: [entry({})], totalMatching: 1, caption: "c" }))).not.toContain("left out");
    const staleHtml = renderToStaticMarkup(createElement(LedgerTable, { entries: [entry({ state: "stale", stale: true, staleAfterHours: 48, staleReason: "last checked 72 h ago, older than 48 h (twice the scheduled refresh gap)" })], caption: "c" }));
    expect(staleHtml).toContain("last checked 72 h ago, older than 48 h (twice the scheduled refresh gap)");
    expect(staleHtml).toContain("stale after 48 h");
    expect(renderToStaticMarkup(createElement(LedgerTable, { entries: [entry({})], caption: "c" }))).toContain("age not judged");
    // the threshold is explained from the backend's basis (regression: it used to say only "stale after 48 h")
    const dflt = renderToStaticMarkup(createElement(LedgerTable, { entries: [entry({ staleAfterHours: 48, staleBasis: "documented_default" })], caption: "c" }));
    expect(dflt).toContain("stale after 48 h (twice the documented default refresh interval)");
    const job = renderToStaticMarkup(createElement(LedgerTable, { entries: [entry({ staleAfterHours: 48, staleBasis: "job_schedule" })], caption: "c" }));
    expect(job).toContain("stale after 48 h (twice the scheduled refresh gap)");
  });
  it("F7-9: a summary strip carries its own pattern definitions (no atlas matrix needed)", () => {
    const data = { groupBy: "source" as const, totalEntries: 2, note: "n", groups: [{ key: "fred", entries: 2, byState: { paid_only: 1, available: 1 }, expectedPeriods: null, availablePeriods: null, entriesWithExpected: 0, availableEntries: 1, coverageRatio: null }] };
    const html = renderToStaticMarkup(createElement(SummaryStrip, { data }));
    const ref = /fill="url\(#([^)]+)\)"/.exec(html);
    expect(ref).not.toBeNull();
    expect(html).toContain(`<pattern id="${ref![1]}"`);
    // the hatch colour is set on the svg that defines the pattern, in a token that exists in both themes
    expect(html).toContain("text-text-muted");
  });
  it("F7-9: a sliver segment drops its icon instead of clipping it", () => {
    const data = { groupBy: "source" as const, totalEntries: 60, note: "n", groups: [{ key: "x", entries: 60, byState: { available: 59, paid_only: 1 }, expectedPeriods: null, availablePeriods: null, entriesWithExpected: 0, availableEntries: 59, coverageRatio: null }] };
    const html = renderToStaticMarkup(createElement(SummaryStrip, { data }));
    const sliver = html.split('title="Paid only')[1]?.split("</span>")[0] ?? "";
    expect(sliver).not.toContain("<svg class=\"lucide");
  });
});

describe("third wave: measured panels", () => {
  const pc = {
    snapshot_date: "2026-10-01", as_of: "2026-10-03", base_currency: "EUR", denominator_base: 1000, usable_value_base: 600, unknown_value_base: 400, usable_share: 0.6, unknown_share: 0.4, target: 0.95, meets_target: false, max_stale_sessions: 3,
    holdings_total: 2, holdings_usable: 1, holdings_stale: 0, holdings_no_price: 1, holdings_basis_unknown: 0,
    holdings: [
      { ticker: "AAA", name: "Alpha", value_base: 600, weight: 0.6, share_of_held_value: 0.6, state: "usable" as const, symbol: "aaa.us", source: "stooq", last_close_date: "2026-10-02", last_close: 12.5, currency: "USD", quote_unit: null, currency_major: "USD", unit_factor: 1, adjustment_basis: "adjusted", stale_sessions: 1, calendar_basis: null, reason: "adjusted close in USD, 1 sessions old" },
      { ticker: "BBB", name: null, value_base: 400, weight: 0.4, share_of_held_value: 0.4, state: "no_price" as const, symbol: null, source: null, last_close_date: null, last_close: null, currency: null, quote_unit: null, currency_major: null, unit_factor: null, adjustment_basis: null, stale_sessions: null, calendar_basis: null, reason: "no stored adjusted close for any of the holding's price symbols" },
    ],
  };
  it("price coverage shows the value share, the unknown bucket and a gap row without a zero price", () => {
    const html = renderToStaticMarkup(createElement(PriceCoveragePanel, { data: pc }));
    expect(html).toContain("60.0% of held market value (600 of 1,000");
    expect(html).toContain("unknown bucket is 40.0%");
    expect(html).toContain("No stored close");
    expect(html).toContain("Not covered");
    expect(html).not.toMatch(/NaN|undefined/);
  });
  it("price coverage with no positions is a placeholder, not 0%", () => {
    const html = renderToStaticMarkup(createElement(PriceCoveragePanel, { data: { ...pc, holdings_total: 0, usable_share: null, unknown_share: null, holdings: [] } }));
    expect(html).toContain("no price coverage is measured");
    expect(html).not.toContain("0.0%");
  });
  it("reach groups switch and unknown buckets are named, not hidden", () => {
    const row = { exchange_mic: null, country: null, asset_class: null, metric: "price", tested_symbols: 2, valid_records: 10, history_first: null, history_last: null, history_count: 0, freshest_known_at: null, coverage_states: {} };
    const data = { universe_note: "own universe only", as_of: "2026-10-03T10:00:00Z", held_tickers: 1, watchlist_tickers: 0, tested_symbols: 2, cells: [], by_exchange: [row], by_country: [], by_asset_class: [] };
    const html = renderToStaticMarkup(createElement(ReachPanel, { data }));
    expect(html).toContain("Unknown exchange");
    expect(html).toContain("No state column");
    expect(html).toContain("own universe only");
    expect(reachRows(data, "country")).toEqual([]);
  });
  it("provenance: a dimension with no column reads 'No column', an empty table 'Empty table'", () => {
    const dim = (dimension: "source" | "as_of" | "known_at" | "method" | "access_state", column: string | null, rows: number) => ({ dimension, column, present: column ? rows : 0, missing: column ? 0 : rows, share: column && rows ? 1 : column ? null : 0 });
    const data = {
      target: 1, meets_target: false, tables_total: 2, tables_meeting_target: 0, empty_tables: ["corporate_action"],
      tables: [
        { table: "peer_set", rows: 4, dimensions: [dim("source", "source", 4), dim("as_of", "as_of", 4), dim("known_at", "known_at", 4), dim("method", "basis", 4), dim("access_state", null, 4)], complete_rows: 4, complete_share: null, target: 1, meets_target: false },
        { table: "corporate_action", rows: 0, dimensions: [dim("source", "source", 0), dim("as_of", "as_of", 0), dim("known_at", "known_at", 0), dim("method", null, 0), dim("access_state", null, 0)], complete_rows: 0, complete_share: null, target: 1, meets_target: false },
      ],
    };
    const html = renderToStaticMarkup(createElement(ProvenancePanel, { data }));
    expect(html).toContain("No column");
    expect(html).toContain("Empty table");
    expect(html).toContain("Not judged");
    expect(html).not.toContain("NaN");
  });
  it("identity lists unresolved exceptions with an unknown score as 'No score'", () => {
    const data = { snapshot_date: "2026-10-01", held_positions: 2, reviewed: 1, owner_reviewed: 1, auto_confirmed: 0, reviewed_share: 0.5, owner_reviewed_share: 0.5, unresolved_count: 1, unresolved: [{ ticker: "ZZZ", name: null, link_status: "no_link", score: null, last_decision: null, last_decision_at: null, identity_error: null }], accounted_share: 1, target: 1, meets_target: false };
    const html = renderToStaticMarkup(createElement(IdentityReviewPanel, { data }));
    expect(html).toContain("1 of 2 held positions (50.0%)");
    expect(html).toContain("No score");
    expect(html).toContain("no link");
  });
});
