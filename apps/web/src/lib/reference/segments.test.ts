import { describe, expect, it } from "vitest";

import type { HoldingSegments, SegmentHistoryPoint, SegmentRow } from "@/lib/api/client";

import {
  availableAxes,
  chartRows,
  formatRevenue,
  OTHER_REGIONS,
  periodCaption,
  regionColumns,
  salesRegionFigure,
  sourceCaption,
} from "./segments";

const row = (label: string, value: number, extra: Partial<SegmentRow> = {}): SegmentRow => ({
  label,
  member: `x:${label}`,
  value,
  share: null,
  subtotal: false,
  concept: "us-gaap:Revenues",
  label_source: "filing",
  ...extra,
});

const period = (label: string, end: string) => ({ start: `${end.slice(0, 4)}-01-01`, end, months: 12, basis: "annual" as const, label });

const point = (label: string, end: string, rows: SegmentRow[]): SegmentHistoryPoint => ({ period: period(label, end), rows });

function data(over: Partial<HoldingSegments> = {}): HoldingSegments {
  return {
    ticker: "ASMLa_EQ",
    basis: "annual",
    available_bases: ["annual"],
    period: period("FY2025", "2025-12-31"),
    currency: "EUR",
    geographic: [row("China", 9.52e9), row("Taiwan", 8.34e9)],
    segment: [],
    product: [row("Product", 24.47e9, { subtotal: true }), row("NXE", 10.45e9)],
    total_revenue: 32.67e9,
    history: {},
    source: {
      kind: "sec_xbrl",
      tier: "SEC EDGAR XBRL (official filing)",
      form: "20-F",
      accession: "0001628280-26-011378",
      filed: "2026-02-11",
      instance_url: null,
      label_url: null,
      filing_url: null,
      note: null,
    },
    as_of: "2026-02-11",
    notes: [],
    ...over,
  };
}

const d = (s: string) => s;

describe("revenue breakdown", () => {
  it("lists only the breakdowns with rows", () => {
    expect(availableAxes(data())).toEqual(["geographic", "product"]);
    expect(availableAxes(null)).toEqual([]);
  });

  it("never draws subtotals and sorts largest first", () => {
    expect(chartRows(data().product)).toEqual([{ label: "NXE", value: 10.45e9 }]);
    expect(chartRows([row("A", 1), row("B", 3), row("C", 0)]).map((r) => r.label)).toEqual(["B", "A"]);
  });

  it("formats revenue in the reporting currency", () => {
    expect(formatRevenue(60_846_000_000, "USD")).toBe("USD 60.8bn");
    expect(formatRevenue("173166000000", "DKK")).toBe("DKK 173bn");
    expect(formatRevenue(4_700_000, "EUR")).toBe("EUR 4.7m");
    expect(formatRevenue(null, "EUR")).toBe("—");
  });

  it("captions the period and the provenance", () => {
    expect(periodCaption(data(), d)).toBe("FY2025 (2025-01-01 – 2025-12-31)");
    const quarter = data({ period: { start: "2026-04-01", end: "2026-06-30", months: 3, basis: "quarter", label: "3M to 30 Jun 2026" } });
    expect(periodCaption(quarter, d)).toBe("3 months, 2026-04-01 – 2026-06-30");
    expect(sourceCaption(data(), d)).toBe("SEC EDGAR XBRL (official filing) · 20-F filed 2026-02-11");
    const manual = data({ source: { kind: "manual", tier: "entered manually", note: "Annual Report 2025" } });
    expect(sourceCaption(manual, d)).toBe("Entered manually · Annual Report 2025");
  });

  it("groups all but the largest regions of the latest period for the figure", () => {
    const history = [
      point("FY2024", "2024-12-31", [row("China", 10), row("Taiwan", 5), row("Korea", 4), row("US", 3), row("Japan", 1)]),
      point("FY2025", "2025-12-31", [row("China", 9), row("Taiwan", 8), row("Korea", 8), row("US", 4), row("Japan", 1)]),
    ];
    const { names, columns } = regionColumns(history, 3);
    expect(names).toEqual(["China", "Taiwan", OTHER_REGIONS]);
    expect(columns.map((c) => [c.label, c.total, c.parts.map((p) => p.value)])).toEqual([
      ["FY2024", 23, [10, 5, 8]],
      ["FY2025", 30, [9, 8, 13]],
    ]);
    const figure = salesRegionFigure(data({ history: { geographic: history } }), d);
    expect(figure?.unitLabel).toBe("EUR");
    const big = salesRegionFigure(data({ history: { geographic: [point("FY2025", "2025-12-31", [row("US", 2e9)])] } }), d);
    expect(big?.unitLabel).toBe("EUR bn");
    expect(big?.divisor).toBe(1e9);
    expect(salesRegionFigure(data({ history: {} }), d)).toBeNull();
  });
});
