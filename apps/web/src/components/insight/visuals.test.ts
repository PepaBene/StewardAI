import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { checkSegments, missingInputs } from "./check-list";
import { clipToDomain, DotStrip, placeLabels, priceVsFair, quantile, winsorDomain } from "./dot-strip";
import { forecastStart } from "./forecast-bars";
import { AXIS_ANGLES_DEG, closedSplinePath, markPoints, meanKnown, scoreRadius, scoreTone } from "./score-mark-geometry";

describe("score mark geometry", () => {
  it("maps scores to radii on the 0.18R..R scale, a zero score keeping a core", () => {
    expect(scoreRadius(0, 100)).toBeCloseTo(18);
    expect(scoreRadius(6, 100)).toBeCloseTo(100);
    expect(scoreRadius(3, 100)).toBeCloseTo(59);
    expect(scoreRadius(null, 100)).toBeCloseTo(18);
    expect(scoreRadius(9, 100)).toBeCloseTo(100);
  });

  it("places the five axes at -90, -18, 54, 126 and 198 degrees, Value at the top", () => {
    expect([...AXIS_ANGLES_DEG]).toEqual([-90, -18, 54, 126, 198]);
    const pts = markPoints([6, 6, 6, 6, null], 50, 100, 100);
    expect(pts[0].x).toBeCloseTo(100);
    expect(pts[0].y).toBeCloseTo(50);
    expect(pts[1].x).toBeCloseTo(100 + 50 * Math.cos((-18 * Math.PI) / 180));
    expect(pts[4].score).toBeNull();
    expect(pts[4].r).toBeCloseTo(9);
  });

  it("tones by the mean of known scores: <2.5 weak, 2.5-4 mixed, >4 strong", () => {
    expect(scoreTone(2.49)).toBe("weak");
    expect(scoreTone(2.5)).toBe("mixed");
    expect(scoreTone(4)).toBe("mixed");
    expect(scoreTone(4.01)).toBe("strong");
    expect(scoreTone(null)).toBeNull();
    expect(meanKnown([6, null, 2])).toBe(4);
    expect(meanKnown([null, null])).toBeNull();
  });

  it("closes the spline with one cubic per point", () => {
    const d = closedSplinePath(markPoints([3, 4, 5, 2, 1], 50, 60, 60));
    expect(d.match(/C/g)?.length).toBe(5);
    expect(d.endsWith("Z")).toBe(true);
    expect(d).not.toMatch(/NaN/);
  });
});

describe("dot strip", () => {
  it("prices versus fair value with negative = cheaper", () => {
    expect(priceVsFair(80, 100)).toBeCloseTo(-0.2);
    expect(priceVsFair(150, 100)).toBeCloseTo(0.5);
    expect(priceVsFair(100, 0)).toBeNull();
    expect(priceVsFair(null, 100)).toBeNull();
  });

  it("winsorises the domain to p2..p98 so one outlier is clipped to the edge", () => {
    const values = [...Array.from({ length: 99 }, (_, i) => i / 100), 50];
    const domain = winsorDomain(values)!;
    expect(domain[1]).toBeLessThan(2);
    expect(domain[0]).toBeLessThan(0.02);
    expect(clipToDomain(50, domain)).toEqual({ value: domain[1], clipped: "high" });
    expect(clipToDomain(0.5, domain).clipped).toBeNull();
    expect(clipToDomain(-9, domain).clipped).toBe("low");
    expect(quantile([1, 2, 3, 4, 5], 0.5)).toBe(3);
  });

  it("puts cheaper (negative) dots in the labelled gain zone on the left", () => {
    const dots = [-0.3, -0.1, 0.2, 0.4].map((v, i) => ({ key: String(i), label: `S${i}`, value: v }));
    const html = renderToStaticMarkup(createElement(DotStrip, { dots, convention: "price-vs-fair", format: (v: number) => `${Math.round(v * 100)}%`, label: "Price vs fair" }));
    expect(html).toContain("Cheaper than fair value");
    expect(html).toContain("Dearer than fair value");
    expect(html.indexOf("Cheaper than fair value")).toBeLessThan(html.indexOf("Dearer than fair value"));
    expect(html).toContain("fill-gain-subtle");
  });

  it("labels at most 12 dots and keeps collisions out of a lane", () => {
    const items = Array.from({ length: 30 }, (_, i) => ({ key: String(i), text: `SYM${i}`, x: 100 + i * 3 }));
    const labels = placeLabels(items, [0, 640]);
    expect(labels.size).toBeLessThanOrEqual(12);
    const above = [...labels.values()].filter((l) => l.lane === "above");
    expect(new Set(above.map((l) => l.x)).size).toBe(above.length);
  });
});

describe("check list and forecast bars helpers", () => {
  it("fills the six segments in proportion, pass then fail", () => {
    expect(checkSegments({ pass: 3, fail: 3, na: 0, unknown: 0 })).toEqual(["pass", "pass", "pass", "fail", "fail", "fail"]);
    expect(checkSegments({ pass: 0, fail: 0, na: 4, unknown: 0 })).toEqual(Array(6).fill("none"));
  });

  it("names each missing input once", () => {
    const checks = [
      { state: "unknown" as const, inputs: { free_cash_flow: null, price: 3 } },
      { state: "unknown" as const, inputs: { free_cash_flow: null, net_debt: null } },
      { state: "pass" as const, inputs: { other: null } },
    ];
    expect(missingInputs(checks)).toEqual(["free cash flow", "net debt"]);
  });

  it("starts the axis at the first non-zero period or 12 before the first estimate, whichever is earlier", () => {
    const hist = (n: number) => ({ total: n, phase: "history" as const });
    const fc = { total: 1, phase: "forecast" as const };
    expect(forecastStart([hist(0), hist(0), hist(2), hist(3)])).toBe(2);
    const long = [...Array.from({ length: 10 }, () => hist(0)), ...Array.from({ length: 10 }, () => hist(1)), fc];
    expect(forecastStart(long)).toBe(8);
    expect(forecastStart([hist(0), hist(0)])).toBe(0);
  });
});
