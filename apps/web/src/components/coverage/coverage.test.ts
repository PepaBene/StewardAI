import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AtlasMatrix } from "@/components/coverage/atlas-matrix";
import { GlobalReach } from "@/components/coverage/global-reach";
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
    expect(renderToStaticMarkup(createElement(Scorecard, { items: [provenanceScore()] }))).toContain("Not measured by the API");
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
