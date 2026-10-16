import { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { monthlyReturns } from "@/lib/charts/series";

import {
  BarChart,
  CandleChart,
  DivergingBar,
  Donut,
  DrawdownChart,
  Gauge,
  GridStatus,
  HBar,
  Heatmap,
  Histogram,
  LineChart,
  Radar,
  Ring,
  rowsFromMonthlyReturns,
  Sankey,
  Sparkline,
  StackedBar,
  Treemap,
} from "./index";

/*
 * Render smoke tests: every component renders sample data without NaN/undefined leaking
 * into the markup, carries an accessible name, and shows the quiet empty state (or nothing)
 * for empty input instead of inventing data.
 */

function render<P extends object>(C: ComponentType<P>, props: P): string {
  return renderToStaticMarkup(createElement(C, props));
}

function days(n: number, start = "2025-10-01"): string[] {
  const t0 = Date.parse(`${start}T00:00:00Z`);
  return Array.from({ length: n }, (_, i) => new Date(t0 + i * 86_400_000).toISOString().slice(0, 10));
}

const dates = days(120);
const series = dates.map((date, i) => ({ date, value: 100 + Math.sin(i / 9) * 4 + i * 0.05 }));
const bench = dates.map((date, i) => ({ date, value: 100 + Math.cos(i / 11) * 3 + i * 0.04 }));

function expectClean(html: string) {
  expect(html).not.toMatch(/NaN|undefined|Infinity/);
}

const EMPTY = 'data-slot="chart-empty"';

describe("chart components", () => {
  it("Sparkline renders a path, or nothing for < 2 points", () => {
    const html = render(Sparkline, { values: [1, 2, null, 3], area: true, tone: "direction", label: "NVDA" });
    expectClean(html);
    expect(html).toContain('role="img"');
    expect(html).toContain("stroke-gain");
    expect(render(Sparkline, { values: [1] })).toBe("");
  });

  it("LineChart draws line, dashed benchmark, month labels and legend", () => {
    const html = render(LineChart, { data: series, benchmark: bench, label: "Portfolio", benchmarkLabel: "VWCE" });
    expectClean(html);
    expect(html).toContain('stroke-dasharray="4 4"');
    expect(html).toContain(">Nov<");
    expect(html).toContain("VWCE");
    const inverse = render(LineChart, { data: series, variant: "inverse" });
    expect(inverse).toContain("stroke-accent-foreground");
    expect(render(LineChart, { data: [{ date: "2026-01-01", value: 1 }] })).toContain(EMPTY);
  });

  it("CandleChart draws candles, or falls back to a close line without OHLC", () => {
    const bars = dates.slice(0, 40).map((date, i) => {
      const o = 100 + i;
      const c = o + (i % 3 === 0 ? -2 : 2);
      return { date, open: o, close: c, high: Math.max(o, c) + 1, low: Math.min(o, c) - 1, volume: 1000 + i };
    });
    const html = render(CandleChart, { bars, maWindow: 5, label: "ASML" });
    expectClean(html);
    expect(html).toContain("fill-loss-subtle");
    expect(html).toContain("MA 5");
    const fallback = render(CandleChart, { bars: bars.map(({ date, close }) => ({ date, close })) });
    expectClean(fallback);
    expect(fallback).toContain("Close only");
    expect(render(CandleChart, { bars: [] })).toContain(EMPTY);
  });

  it("DrawdownChart marks the trough", () => {
    const html = render(DrawdownChart, { data: [100, 110, 90, 95, 105].map((value, i) => ({ date: dates[i], value })) });
    expectClean(html);
    expect(html).toContain("−18.2%");
    expect(render(DrawdownChart, { data: [] })).toContain(EMPTY);
  });

  it("Donut, Ring and Gauge", () => {
    const donut = render(Donut, {
      segments: Array.from({ length: 10 }, (_, i) => ({ label: `S${i}`, value: 10 - i })),
      centerValue: "10",
      centerLabel: "positions",
    });
    expectClean(donut);
    expect(donut).toContain("Other 3");
    expect(donut).toContain("fill-categorical-8");
    expect(render(Donut, { segments: [] })).toContain(EMPTY);

    const ring = render(Ring, { value: 88, ariaLabel: "Thesis score" });
    expectClean(ring);
    expect(ring).toContain('role="meter"');
    expect(ring).toContain(">88<");
    expect(render(Ring, { value: null })).toContain("—");

    const gauge = render(Gauge, { value: 9.8, max: 12, sublabel: "of 12 positions" });
    expectClean(gauge);
    expect(gauge).toContain(">9.8<");
  });

  it("Radar draws up to two series and a hidden table", () => {
    const html = render(Radar, {
      axes: ["A", "B", "C", "D", "E"],
      series: [
        { label: "Now", values: [0.5, 0.6, 0.7, 0.8, 0.9] },
        { label: "Q1", values: [0.4, 0.5, 0.6, 0.7, 0.8] },
      ],
    });
    expectClean(html);
    expect(html).toContain('stroke-dasharray="3 3"');
    expect(html).toContain("<caption>");
    expect(render(Radar, { axes: ["A", "B"], series: [] })).toContain(EMPTY);
  });

  it("Heatmap renders a table from monthly returns with dashed empty cells", () => {
    const daily = days(200, "2025-06-01").map((date, i) => ({ date, value: 100 * Math.pow(1.001, i) * (1 + Math.sin(i / 17) / 20) }));
    const rows = rowsFromMonthlyReturns(monthlyReturns(daily));
    const html = render(Heatmap, { rows, caption: "Monthly returns" });
    expectClean(html);
    expect(html).toContain("<table");
    expect(html).toContain("border-dashed");
    expect(html).toMatch(/bg-heat-(up|down)/);
    expect(render(Heatmap, { rows: [] })).toContain(EMPTY);
  });

  it("Treemap lays out tiles with heat colours", () => {
    const html = render(Treemap, {
      items: [
        { id: "a", label: "VWCE", weight: 0.18, change: 0.004 },
        { id: "b", label: "ASML", weight: 0.1, change: -0.018 },
        { id: "c", label: "NOVO", weight: 0.06, change: -0.031 },
        { id: "d", label: "NEW", weight: 0.02, change: null },
      ],
      label: "Daily move",
    });
    expectClean(html);
    expect(html).toContain("bg-heat-down-strong");
    expect(html).toContain("bg-chart-unknown");
    expect(render(Treemap, { items: [] })).toContain(EMPTY);
  });

  it("Sankey draws ribbons, nodes and a flow table", () => {
    const html = render(Sankey, {
      nodes: [
        { id: "dep", label: "Deposits", column: 0 },
        { id: "div", label: "Dividends", column: 0 },
        { id: "int", label: "Interest", column: 0 },
        { id: "av", label: "Available", column: 1 },
        { id: "stk", label: "Stocks", column: 2 },
        { id: "fee", label: "FX & fees", column: 2 },
      ],
      links: [
        { source: "dep", target: "av", value: 360, emphasis: true },
        { source: "div", target: "av", value: 21.4 },
        { source: "int", target: "av", value: 3.1 },
        { source: "av", target: "stk", value: 382.6, emphasis: true },
        { source: "av", target: "fee", value: 1.9 },
      ],
    });
    expectClean(html);
    expect(html).toContain("Deposits → Available");
    expect(render(Sankey, { nodes: [], links: [] })).toContain(EMPTY);
  });

  it("BarChart, Histogram", () => {
    const bars = render(BarChart, {
      data: [
        { label: "Q1", value: 3.2 },
        { label: "Q2", value: null },
        { label: "Q3", value: 5.5, claim: true },
        { label: "Q4", value: 4.1 },
      ],
      highlightLast: true,
      threshold: { value: 4, label: "Threshold 4" },
    });
    expectClean(bars);
    expect(bars).toContain("fill-accent-300");
    expect(bars).toContain("Threshold 4");
    expect(bars).toContain("No data");
    expect(render(BarChart, { data: [] })).toContain(EMPTY);

    const hist = render(Histogram, { values: [1, 2, 2, 3, 3, 3, 8, 13], marker: 3 });
    expectClean(hist);
    expect(hist).toContain("fill-accent");
    expect(render(Histogram, { values: [] })).toContain(EMPTY);
  });

  it("HBar, DivergingBar, StackedBar", () => {
    const meter = render(HBar, { value: 0.098, target: 0.09, band: [0.07, 0.11], max: 0.15, label: "Weight" });
    expectClean(meter);
    expect(meter).toContain('role="meter"');

    const drift = render(DivergingBar, {
      rows: [
        { label: "VWCE", value: -3.6, band: 3 },
        { label: "NVDA", value: 2.2, band: 2 },
        { label: "SAP", value: -0.1, band: 2 },
      ],
    });
    expectClean(drift);
    expect(drift).toContain("outside band");
    expect(render(DivergingBar, { rows: [] })).toContain(EMPTY);

    const stacked = render(StackedBar, {
      title: "Sector",
      segments: [
        { label: "Global equity", value: 0.31 },
        { label: "Semis", value: 0.24 },
      ],
    });
    expectClean(stacked);
    expect(stacked).toContain("Global equity 56%");
  });

  it("GridStatus renders cells and a words-only table", () => {
    const html = render(GridStatus, {
      columns: ["1 Sep", "2 Sep", "3 Sep"],
      rows: [
        { label: "Prices", cells: ["ok", "retried", "failed"], note: "18:10" },
        { label: "13F", cells: ["none"] },
      ],
    });
    expectClean(html);
    expect(html).toContain("bg-loss");
    expect(html).toContain("Not scheduled");
    expect(render(GridStatus, { rows: [], columns: [] })).toContain(EMPTY);
  });
});
