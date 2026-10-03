import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CriteriaEditor, cleanNumberText } from "@/components/screens/criteria-editor";
import { ResultsTable, SummaryTiles, UniverseTable } from "@/components/screens/results";
import { runScreen, summarize } from "@/lib/screens/logic";
import type { Criterion, Entity, ScreenInputs } from "@/lib/screens/types";

const crit: Criterion[] = [{ metric: "pe", op: "lte", value: "20" }];
const entities: Entity[] = [
  { ticker: "A", name: "Alpha", held: true, watchlist: false },
  { ticker: "B", name: null, held: false, watchlist: true },
];
const inputs: ScreenInputs = {
  held: [],
  watchlist: [],
  prices: [],
  valuations: new Map([["A", { kind: "error", message: "HTTP 500" }], ["B", { kind: "not_found" }]]),
  ledger: new Map(),
};

describe("screen components render", () => {
  it("results with unknown, not-covered and error entities", () => {
    const outcomes = runScreen(entities, crit, inputs, "exclude");
    const html = renderToStaticMarkup(createElement(ResultsTable, { outcomes, criteria: crit }));
    expect(html).toContain("Excluded");
    expect(html).toContain("Not covered");
    expect(html).toContain("Temporarily unavailable");
    expect(renderToStaticMarkup(createElement(ResultsTable, { outcomes: [], criteria: crit }))).toContain("No entity");
  });
  it("summary tiles show universe size and excluded count; no criteria shows n/a", () => {
    const s = summarize(runScreen(entities, crit, inputs, "exclude"));
    const html = renderToStaticMarkup(createElement(SummaryTiles, { s, treatment: "exclude", hasCriteria: true }));
    expect(html).toContain("Universe size");
    expect(html).toContain("Excluded");
    expect(renderToStaticMarkup(createElement(SummaryTiles, { s, treatment: "unknown", hasCriteria: false }))).toContain("n/a");
  });
  it("universe table and empty universe", () => {
    expect(renderToStaticMarkup(createElement(UniverseTable, { entities }))).toContain("watchlist");
    expect(renderToStaticMarkup(createElement(UniverseTable, { entities: [] }))).toContain("empty");
  });
  it("criteria editor flags an incomplete threshold", () => {
    const html = renderToStaticMarkup(
      createElement(CriteriaEditor, { criteria: [{ metric: "pe", op: "lte", value: "" }], onChange: () => {}, missing: "exclude", onMissing: () => {}, scope: "all", onScope: () => {} }),
    );
    expect(html).toContain("ignored until then");
    expect(cleanNumberText("1,2:3e")).toBe("123");
  });
});
