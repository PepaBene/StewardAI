import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  COLUMN_IDS,
  COLUMNS,
  DEFAULT_CUSTOM,
  PRESETS,
  dropColumn,
  insightNumber,
  missingReason,
  moveColumn,
  nextSortState,
  presetColumns,
  priceWithFair,
  sortRows,
  toggleColumn,
  type HoldingInsight,
  type RowSource,
} from "./holding-columns";

const row = (ticker: string, extra: Partial<HoldingInsight> = {}): HoldingInsight => ({
  ticker,
  name: ticker,
  fair_stale: false,
  notes: 0,
  metrics: {},
  metric_reasons: {},
  mark: {},
  ...extra,
});
const pos = (ticker: string, value: number): RowSource => ({
  ticker,
  name: ticker,
  quantity: 1,
  avg_price: 1,
  current_price: 1,
  value_base: value,
  weight: value / 100,
  drift: null,
  ppl: 0,
});

describe("holding columns", () => {
  it("matches the backend's column list", () => {
    const py = readFileSync(resolve(__dirname, "../../../../backend/src/steward/config/holdings_views.py"), "utf8");
    const block = py.slice(py.indexOf("COLUMN_IDS"), py.indexOf(")  # fmt: skip"));
    const ids = [...block.matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]);
    expect(new Set(ids)).toEqual(new Set(COLUMN_IDS));
  });

  it("defines every column and every preset starts with the holding", () => {
    for (const id of COLUMN_IDS) expect(COLUMNS[id].id).toBe(id);
    for (const p of PRESETS.filter((x) => x.key !== "custom")) expect(p.columns[0]).toBe("holding");
  });

  it("cleans a saved custom view", () => {
    expect(presetColumns("custom", ["price", "nope", "price"])).toEqual(["holding", "price"]);
    expect(presetColumns("custom", [])).toEqual([...DEFAULT_CUSTOM]);
    expect(presetColumns("income", null)[1]).toBe("value");
  });

  it("keeps the holding first when reordering", () => {
    const cols = presetColumns("custom", ["holding", "price", "value", "notes"]);
    expect(moveColumn(cols, "price", -1)).toEqual(cols);
    expect(moveColumn(cols, "value", -1)).toEqual(["holding", "value", "price", "notes"]);
    expect(dropColumn(cols, "notes", "price")).toEqual(["holding", "notes", "price", "value"]);
    expect(dropColumn(cols, "notes", "holding")).toEqual(["holding", "notes", "price", "value"]);
    expect(toggleColumn(cols, "holding")).toEqual(cols);
    expect(toggleColumn(cols, "roe")).toContain("roe");
  });

  it("reads metrics and states why one is missing", () => {
    const r = row("A", { metrics: { pe_ratio: "12.5", roe: null }, metric_reasons: { roe: "no equity" } });
    expect(insightNumber("pe", r)).toBe(12.5);
    expect(insightNumber("roe", r)).toBeNull();
    expect(missingReason("roe", r)).toBe("no equity");
  });

  it("steps the fair value under the closes", () => {
    const bars = [
      { date: "2026-01-01", close: 10 },
      { date: "2026-01-02", close: 11 },
      { date: "2026-01-05", close: 12 },
    ];
    const hist = [
      { as_of: "2026-01-02", value: "15" },
      { as_of: "2025-12-01", value: 14 },
    ];
    expect(priceWithFair(bars, hist).map((r) => r.fair)).toEqual([14, 15, 15]);
    expect(priceWithFair([], hist).map((r) => r.date)).toEqual(["2025-12-01", "2026-01-02"]);
  });

  it("sorts missing values last in both directions", () => {
    const insight = new Map([
      ["A", row("A", { metrics: { pe_ratio: 10 } })],
      ["B", row("B", { metrics: { pe_ratio: null } })],
      ["C", row("C", { metrics: { pe_ratio: 20 } })],
    ]);
    const rows = [pos("A", 1), pos("B", 2), pos("C", 3)];
    const day = () => null;
    expect(sortRows(rows, { id: "pe", dir: "desc" }, insight, day).map((r) => r.ticker)).toEqual(["C", "A", "B"]);
    expect(sortRows(rows, { id: "pe", dir: "asc" }, insight, day).map((r) => r.ticker)).toEqual(["A", "C", "B"]);
    expect(nextSortState({ id: "pe", dir: "desc" }, "pe").dir).toBe("asc");
    expect(nextSortState({ id: "pe", dir: "desc" }, "holding").dir).toBe("asc");
  });
});
