import { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { monthBars, petalsFromMap, type Check } from "@/lib/insight/insight";

import { BandGauge } from "./band-gauge";
import { CalcChain, formatStep } from "./calc-chain";
import { ChartWithFooter } from "./chart-footer";
import { CheckList } from "./check-list";
import { DotStrip } from "./dot-strip";
import { ForecastBars } from "./forecast-bars";
import { GroupedTable } from "./grouped-table";
import { MonthCalendar } from "./month-calendar";
import { ScoreMark } from "./score-mark";
import { SliderRange } from "./slider-range";
import { StepLineChart, StepSparkline } from "./step-line";
import { VerdictLine } from "./verdict-line";

/*
 * Render smoke tests for the insight components: sample data renders without NaN/undefined in the
 * markup, every data-dense picture carries a table twin, and unknown values are never drawn as 0.
 */

function render<P extends object>(C: ComponentType<P>, props: P): string {
  return renderToStaticMarkup(createElement(C, props));
}

function clean(html: string) {
  expect(html).not.toMatch(/NaN|undefined/);
}

const check = (id: string, state: Check["state"]): Check => ({
  id,
  axis: "health",
  label: id,
  state,
  value: null,
  threshold: null,
  unit: null,
  basis: null,
  sentence: `Sentence ${id}`,
  inputs: {},
  section_ref: "ratios",
  method_version: "1",
  informational: false,
});

describe("insight components", () => {
  it("score mark draws unknown axes as open gaps and names every axis", () => {
    const html = render(ScoreMark, { petals: petalsFromMap({ value: 6, past: 3 }), size: "header", label: "Acme" });
    clean(html);
    expect(html).toContain("Future unknown");
    expect(html).toContain("Value 6 of 6");
    expect(html.match(/stroke-dasharray/g)?.length).toBe(3);
    const card = render(ScoreMark, { petals: petalsFromMap({ value: 2 }), size: "card", onSelect: () => {} });
    expect(card).toContain("Show Dividend");
  });

  it("check list counts only applicable checks and links the section", () => {
    const html = render(CheckList, { checks: [check("a", "pass"), check("b", "fail"), check("c", "na"), check("d", "unknown")], ticker: "AAA_US_EQ", failedFirst: true });
    clean(html);
    expect(html).toContain("1 of 2");
    expect(html).toContain("1 unknown, 1 not applicable");
    expect(html.indexOf("Sentence b")).toBeLessThan(html.indexOf("Sentence a"));
    expect(html).toContain("/companies/AAA_US_EQ/ratios");
  });

  it("forecast bars carry a table twin with every period", () => {
    const bars = monthBars([
      { month: "2026-09", phase: "history", received: 12, declared: 0, estimated: 0, payments: [] },
      { month: "2026-11", phase: "forecast", received: 0, declared: 3, estimated: 4, payments: [] },
    ]);
    const html = render(ForecastBars, { bars, format: (v: number) => v.toFixed(2), label: "Income" });
    clean(html);
    expect(html).toContain("<table>");
    expect(html).toContain("Nov 26: 7.00, of which 4.00 estimated");
  });

  it("month calendar lists events with their type", () => {
    const html = render(MonthCalendar, {
      month: "2026-10",
      firstDay: "2026-10-01",
      items: [{ key: "1", date: "2026-10-14", kind: "payment", title: "AAA", amount: "USD 2.50" }],
      kinds: [{ key: "payment", label: "Payment", dot: "bg-chart-line" }],
      onMonth: () => {},
      today: "2026-10-16",
    });
    clean(html);
    expect(html).toContain("Oct 2026");
    expect(html).toContain("AAA");
    expect(html).toContain(", Payment");
  });

  it("step line chart and sparkline render the stored steps only", () => {
    const rows = [
      { date: "2026-10-01", value: 10, fair: null },
      { date: "2026-10-02", value: 11, fair: 12 },
      { date: "2026-10-03", value: 12, fair: 12 },
    ];
    const html = render(StepLineChart, { rows, valueLabel: "Price", fairLabel: "Fair value", format: (v: number) => v.toFixed(0), label: "Price vs fair value", today: "2026-10-03" });
    clean(html);
    expect(html).toContain("Fair value recorded from here");
    clean(render(StepSparkline, { rows }));
  });

  it("dot strip, gauge, chain, grouped table, slider and verdict render cleanly", () => {
    clean(render(DotStrip, { dots: [{ key: "a", label: "A", value: 1, highlight: true }, { key: "b", label: "B", value: null }], refs: [{ label: "Median", value: 0.5 }], format: String, label: "x" }));
    clean(render(BandGauge, { value: 0.1, min: -0.5, max: 0.5, zones: [{ label: "Under", to: -0.2, tone: "gain" }, { label: "About right", to: 0.2, tone: "neutral" }, { label: "Over", to: 0.5, tone: "loss" }], marker: "10% over", format: String, label: "DCF" }));
    expect(formatStep("0.125", "fraction")).toBe("12.5%");
    expect(formatStep(1500, "USD")).toContain("USD");
    clean(render(CalcChain, { steps: [{ label: "Rev", expression: "100 × 1.1", value: 110, unit: "USD" }], caption: "c" }));
    const g = render(GroupedTable<{ id: string }>, {
      groups: [
        { key: "a", title: "Over", explanation: "e", rows: [{ id: "x" }] },
        { key: "b", title: "Under", explanation: "e", rows: [] },
      ],
      columns: [{ key: "id", header: "Id", cell: (r: { id: string }) => r.id }],
      rowKey: (r: { id: string }) => r.id,
      caption: "g",
    });
    expect(g).toContain("None");
    clean(render(SliderRange, { ticks: [0, 1, 10], value: [null, 5], onChange: () => {}, format: String, label: "Yield" }));
    clean(render(VerdictLine, { verdict: "good", children: "Fine" }));
    clean(render(ChartWithFooter, { chart: "c", table: "t", dataset: "d", asOf: "2026-10-16", learnHref: "/glossary" }));
  });
});
