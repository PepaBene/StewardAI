import { describe, expect, it } from "vitest";

import { annularSector, clamp01, donutSlices, gaugeArcs, radarLayout, ringDash, TAU } from "./arc";
import { hasCompleteOhlc, hasVolume, isCompleteBar } from "./candles";
import { formatCompact, formatNumber, formatPct, formatSigned, MINUS } from "./format";
import { heatLevel, heatNeedsLightText } from "./heat";
import { binValues } from "./histogram";
import { areaPath, linePath, segments } from "./path";
import { sankeyLayout, spreadLabels } from "./sankey";
import { bandScale, extent, linearScale, niceDomain, niceStep, niceTicks } from "./scale";
import { drawdown, maxDrawdown, monthlyReturns, movingAverage, rebase } from "./series";
import { squarify } from "./squarify";
import { formatDay, parseDay, timeTicks } from "./time";

/** Element-wise toBeCloseTo that lets nulls through. */
function expectClose(actual: (number | null)[], expected: (number | null)[]) {
  expect(actual).toHaveLength(expected.length);
  actual.forEach((v, i) => {
    const e = expected[i];
    if (e === null) expect(v).toBeNull();
    else expect(v).toBeCloseTo(e, 9);
  });
}

describe("scale", () => {
  it("extent skips gaps and returns null for no data", () => {
    expect(extent([3, null, -1, Number.NaN, 7])).toEqual([-1, 7]);
    expect(extent([null, undefined])).toBeNull();
  });

  it("linearScale maps and inverts direction; flat domain maps to the middle", () => {
    const y = linearScale([0, 10], [100, 0]);
    expect(y(0)).toBe(100);
    expect(y(5)).toBe(50);
    expect(linearScale([4, 4], [0, 10])(4)).toBe(5);
  });

  it("niceStep picks 1/2/2.5/5 multiples", () => {
    expect(niceStep(10, 5)).toBe(2);
    expect(niceStep(0.9, 4)).toBe(0.25);
    expect(niceStep(700, 4)).toBe(200);
  });

  it("niceTicks cover the data on round numbers without float dust", () => {
    expect(niceTicks(94.2, 108.7, 4)).toEqual([90, 95, 100, 105, 110]);
    expect(niceTicks(0.1, 0.3, 2)).toEqual([0.1, 0.2, 0.3]);
    expect(niceTicks(0.05, 0.3, 2)).toEqual([0, 0.2, 0.4]);
    expect(niceDomain(94.2, 108.7, 4)).toEqual([90, 110]);
    expect(niceTicks(5, 5).length).toBeGreaterThan(1);
  });

  it("bandScale caps the bar width and centres it in its step", () => {
    const b = bandScale(4, [0, 400], { paddingInner: 0.2, maxBandwidth: 24 });
    expect(b.step).toBe(100);
    expect(b.bandwidth).toBe(24);
    expect(b.x(1)).toBe(100 + 38);
  });
});

describe("path", () => {
  it("breaks lines at gaps", () => {
    const pts = [[0, 0], [1, 1], null, [3, 3], [4, 4]] as const;
    expect(segments(pts)).toHaveLength(2);
    expect(linePath(pts)).toBe("M0 0 L1 1 M3 3 L4 4");
  });

  it("closes areas to the baseline per segment and skips single points", () => {
    expect(areaPath([[0, 5], [10, 2], null, [20, 1]], 10)).toBe("M0 5 L10 2 L10 10 L0 10 Z");
  });
});

describe("format", () => {
  it("uses a true minus sign and signs positives", () => {
    expect(formatSigned(1.8)).toBe("+1.8");
    expect(formatSigned(-0.6)).toBe(`${MINUS}0.6`);
    expect(formatSigned(0.001)).toBe("0.0");
    expect(formatPct(0.087, 1, true)).toBe("+8.7%");
    expect(formatNumber(-1234.5, 1)).toBe(`${MINUS}1,234.5`);
    expect(formatCompact(2_486_300)).toBe("2.49M");
    expect(formatCompact(104_400)).toBe("104.4k");
  });
});

describe("time", () => {
  it("parses calendar days without time-zone drift", () => {
    expect(parseDay("2026-09-27T23:30:00-05:00")).toEqual({ year: 2026, month: 8, day: 27 });
    expect(parseDay("nope")).toBeNull();
    expect(formatDay("2026-09-27")).toBe("27 Sep 2026");
  });

  it("ticks at month boundaries, skipping a mid-month first point", () => {
    const dates = ["2025-09-27", "2025-09-30", "2025-10-01", "2025-10-15", "2025-11-03", "2025-12-01"];
    expect(timeTicks(dates)).toEqual([
      { index: 2, label: "Oct" },
      { index: 4, label: "Nov" },
      { index: 5, label: "Dec" },
    ]);
    expect(timeTicks(["2025-10-02", "2025-10-20"])).toEqual([{ index: 0, label: "Oct" }]);
  });

  it("switches to year ticks for long series and thins to maxTicks", () => {
    const dates: string[] = [];
    for (let y = 2020; y <= 2025; y++) for (let m = 1; m <= 12; m++) dates.push(`${y}-${String(m).padStart(2, "0")}-01`);
    const ticks = timeTicks(dates);
    expect(ticks.map((t) => t.label)).toEqual(["2020", "2021", "2022", "2023", "2024", "2025"]);
    expect(timeTicks(dates, 3).length).toBeLessThanOrEqual(3);
  });
});

describe("series", () => {
  it("moving average waits for a full window and respects gaps", () => {
    expect(movingAverage([1, 2, 3, 4], 2)).toEqual([null, 1.5, 2.5, 3.5]);
    expect(movingAverage([1, null, 3, 4, 5], 2)).toEqual([null, null, null, 3.5, 4.5]);
  });

  it("drawdown runs from the running peak", () => {
    expectClose(drawdown([100, 110, 99, null, 121, 60.5]), [0, 0, -0.1, null, 0, -0.5]);
    expect(maxDrawdown([100, 110, 99, 121, 60.5])).toEqual({ value: -0.5, index: 4, peakIndex: 3 });
    expect(maxDrawdown([null, null])).toBeNull();
  });

  it("rebases to 100 from the first finite value", () => {
    expectClose(rebase([null, 50, 55, 45]), [null, 100, 110, 90]);
    expect(rebase([null])).toEqual([null]);
  });

  it("buckets a daily index into calendar-month returns", () => {
    const rows = monthlyReturns([
      { date: "2025-11-20", value: 100 },
      { date: "2025-11-28", value: 102 },
      { date: "2025-12-15", value: 99 },
      { date: "2025-12-31", value: 107.1 },
      { date: "2026-01-30", value: 96.39 },
      { date: "2026-03-31", value: 100 }, // February missing -> March is null, not a 2-month return
    ]);
    expect(rows).toHaveLength(2);
    expect(rows[0].year).toBe(2025);
    expect(rows[0].months[10]).toBeCloseTo(0.02); // first (partial) month from the first value
    expect(rows[0].months[11]).toBeCloseTo(0.05);
    expect(rows[0].total).toBeCloseTo(1.02 * 1.05 - 1);
    expect(rows[1].months[0]).toBeCloseTo(-0.1);
    expect(rows[1].months[1]).toBeNull();
    expect(rows[1].months[2]).toBeNull();
    expect(rows[1].total).toBeCloseTo(-0.1);
  });

  it("returns no rows for too little data", () => {
    expect(monthlyReturns([{ date: "2026-01-02", value: 1 }])).toEqual([]);
    expect(monthlyReturns([])).toEqual([]);
  });
});

describe("histogram", () => {
  it("bins equal widths, closing the last bin", () => {
    const bins = binValues([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], { count: 5 });
    expect(bins.map((b) => b.count)).toEqual([2, 2, 2, 2, 3]);
    expect(bins[0]).toMatchObject({ x0: 0, x1: 2 });
    expect(bins[4].x1).toBe(10);
  });

  it("honours thresholds and drops out-of-range values", () => {
    const bins = binValues([1, 5, 12, 30, 90, 200], { thresholds: [0, 10, 60, 120] });
    expect(bins.map((b) => b.count)).toEqual([2, 2, 1]);
  });

  it("handles empty and constant input", () => {
    expect(binValues([])).toEqual([]);
    expect(binValues([null, 3, 3])).toEqual([{ x0: 3, x1: 3, count: 2 }]);
  });
});

describe("arc", () => {
  it("draws sectors, and a full turn as two halves", () => {
    expect(annularSector(0, 0, 5, 10, 0, Math.PI / 2)).toBe("M10 0 A10 10 0 0 1 0 10 L0 5 A5 5 0 0 0 5 0 Z");
    expect(annularSector(0, 0, 5, 10, 0, TAU).match(/M/g)).toHaveLength(2);
    expect(annularSector(0, 0, 5, 10, 1, 1)).toBe("");
  });

  it("donut slices are proportional and skip zeros", () => {
    const slices = donutSlices([1, 0, 3], { cx: 50, cy: 50, r0: 30, r1: 40 });
    expect(slices.map((s) => s.index)).toEqual([0, 2]);
    expect(slices[0].fraction).toBeCloseTo(0.25);
    expect(slices[1].a1 - slices[0].a0).toBeCloseTo(TAU);
    expect(donutSlices([0, 0], { cx: 0, cy: 0, r0: 1, r1: 2 })).toEqual([]);
  });

  it("gauge, ring and clamp", () => {
    const g = gaugeArcs(0.5, { cx: 100, cy: 100, r0: 60, r1: 80 });
    expect(g.ticks).toHaveLength(5);
    expect(g.value).not.toBe("");
    expect(gaugeArcs(0, { cx: 0, cy: 0, r0: 1, r1: 2 }).value).toBe("");
    expect(ringDash(0.5, 10)).toBe(`${Math.round(Math.PI * 10 * 100) / 100} ${Math.round(TAU * 10 * 100) / 100}`);
    expect(clamp01(2)).toBe(1);
    expect(clamp01(Number.NaN)).toBe(0);
  });

  it("radar puts the first axis at 12 o'clock", () => {
    const r = radarLayout(4, 0, 0, 10);
    const [x, y] = r.point(0, 1);
    expect(x).toBeCloseTo(0);
    expect(y).toBeCloseTo(-10);
    expect(r.label(0).anchor).toBe("middle");
    expect(r.label(1).anchor).toBe("start");
    expect(r.label(3).anchor).toBe("end");
  });
});

describe("squarify", () => {
  const rect = { x: 0, y: 0, w: 600, h: 400 };
  const weights = [6, 6, 4, 3, 2, 2, 1];
  const tiles = squarify(weights, (w) => w, rect);

  it("tiles fill the rectangle with areas proportional to weight", () => {
    const total = weights.reduce((s, w) => s + w, 0);
    expect(tiles).toHaveLength(weights.length);
    const area = tiles.reduce((s, t) => s + t.w * t.h, 0);
    expect(area).toBeCloseTo(rect.w * rect.h, 6);
    for (const t of tiles) expect((t.w * t.h) / (rect.w * rect.h)).toBeCloseTo(t.weight / total, 6);
  });

  it("stays inside the rectangle, without overlaps, with sane aspect ratios", () => {
    for (const t of tiles) {
      expect(t.x).toBeGreaterThanOrEqual(-1e-9);
      expect(t.y).toBeGreaterThanOrEqual(-1e-9);
      expect(t.x + t.w).toBeLessThanOrEqual(rect.w + 1e-9);
      expect(t.y + t.h).toBeLessThanOrEqual(rect.h + 1e-9);
      expect(Math.max(t.w / t.h, t.h / t.w)).toBeLessThan(4);
    }
    for (let i = 0; i < tiles.length; i++) {
      for (let j = i + 1; j < tiles.length; j++) {
        const a = tiles[i];
        const b = tiles[j];
        const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        expect(ox > 1e-6 && oy > 1e-6).toBe(false);
      }
    }
  });

  it("drops non-positive weights and keeps input indices", () => {
    const t = squarify([0, 5, -1, 5], (w) => w, rect);
    expect(t.map((x) => x.index).sort()).toEqual([1, 3]);
    expect(squarify([], (w: number) => w, rect)).toEqual([]);
  });
});

describe("sankey", () => {
  const nodes = [
    { id: "dep", label: "Deposits", column: 0 },
    { id: "div", label: "Dividends", column: 0 },
    { id: "av", label: "Available", column: 1 },
    { id: "stk", label: "Stocks", column: 2 },
    { id: "csh", label: "Cash kept", column: 2 },
    { id: "unused", label: "Unused", column: 2 },
  ];
  const links = [
    { source: "dep", target: "av", value: 300, emphasis: true },
    { source: "div", target: "av", value: 100 },
    { source: "av", target: "stk", value: 350 },
    { source: "av", target: "csh", value: 50 },
    { source: "av", target: "missing", value: 10 },
  ];
  const layout = sankeyLayout(nodes, links, { width: 600, height: 250, nodeWidth: 10, gap: 10 })!;

  it("drops nodes without flow and links to unknown nodes", () => {
    expect(layout.nodes.map((n) => n.id)).not.toContain("unused");
    expect(layout.ribbons).toHaveLength(4);
    expect(layout.columns).toBe(3);
  });

  it("sizes nodes and ribbons on one scale", () => {
    const byId = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
    expect(byId.av.h).toBeCloseTo(byId.dep.h + byId.div.h);
    expect(byId.dep.h / byId.div.h).toBeCloseTo(3);
    const r = layout.ribbons.find((x) => x.source === "dep")!;
    expect(r.width).toBeCloseTo(byId.dep.h);
    expect(r.emphasis).toBe(true);
    expect(byId.dep.x).toBe(0);
    expect(byId.stk.x).toBe(590);
    for (const node of layout.nodes) {
      expect(node.y).toBeGreaterThanOrEqual(-1e-9);
      expect(node.y + node.h).toBeLessThanOrEqual(250 + 1e-9);
    }
  });

  it("returns null with no flow", () => {
    expect(sankeyLayout(nodes, [], { width: 10, height: 10 })).toBeNull();
  });

  it("spreadLabels keeps order and minimum spacing within bounds", () => {
    expect(spreadLabels([10, 12, 13], 10, 0, 100)).toEqual([10, 20, 30]);
    expect(spreadLabels([95, 96], 10, 0, 100)).toEqual([90, 100]);
    expect(spreadLabels([50, 10], 10)).toEqual([50, 10]);
  });
});

describe("heat", () => {
  it("buckets into three steps each side of flat", () => {
    expect(heatLevel(0.01, 0.06)).toBe("up-soft");
    expect(heatLevel(0.03, 0.06)).toBe("up");
    expect(heatLevel(0.09, 0.06)).toBe("up-strong");
    expect(heatLevel(-0.058, 0.06)).toBe("down-strong");
    expect(heatLevel(0, 0.06)).toBe("flat");
    expect(heatLevel(null, 0.06)).toBeNull();
    expect(heatNeedsLightText("down-strong")).toBe(true);
    expect(heatNeedsLightText("up-soft")).toBe(false);
  });
});

describe("candles", () => {
  it("requires full, consistent OHLC on every priced bar", () => {
    const good = { date: "2026-01-02", open: 10, high: 12, low: 9, close: 11, volume: 5 };
    expect(isCompleteBar(good)).toBe(true);
    expect(isCompleteBar({ ...good, high: 10.5 })).toBe(false);
    expect(hasCompleteOhlc([good, { date: "2026-01-03", close: 11.5 }])).toBe(false);
    expect(hasCompleteOhlc([good, { date: "2026-01-03", close: null }])).toBe(true);
    expect(hasCompleteOhlc([])).toBe(false);
    expect(hasVolume([good])).toBe(true);
    expect(hasVolume([{ date: "x", close: 1 }])).toBe(false);
  });
});
