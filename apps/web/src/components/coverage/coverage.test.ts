import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AtlasMatrix } from "@/components/coverage/atlas-matrix";
import { GlobalReach } from "@/components/coverage/global-reach";
import { PlanGaps } from "@/components/coverage/plan-gaps";
import { Scorecard } from "@/components/coverage/scorecard";
import { SummaryStrip } from "@/components/coverage/summary-strip";
import { LedgerTable } from "@/components/coverage/ledger-table";
import { buildAtlas, provenanceScore } from "@/lib/coverage/logic";
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
