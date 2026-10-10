import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FanChart } from "./fan-chart";

const fan = {
  p05: [1, 0.9, 0.85, 0.8],
  p25: [1, 0.98, 1.0, 1.02],
  p50: [1, 1.02, 1.06, 1.1],
  p75: [1, 1.06, 1.14, 1.25],
  p95: [1, 1.12, 1.3, 1.5],
};

describe("FanChart", () => {
  it("draws two bands, a median line and a starting-value line, with a spoken summary", () => {
    const html = renderToStaticMarkup(createElement(FanChart, { fan, stepWeeks: 52, years: 3 }));
    expect(html).toContain('role="img"');
    expect(html).toContain("middle of the range 110, bad case 80, good case 150");
    expect((html.match(/<path/g) ?? []).length).toBe(3);
    expect(html).toContain("3 years");
    expect(html).toContain("stroke-dasharray");
  });

  it("draws nothing when a percentile is missing", () => {
    const partial = { p05: fan.p05, p25: fan.p25, p50: fan.p50, p75: fan.p75 };
    expect(renderToStaticMarkup(createElement(FanChart, { fan: partial, stepWeeks: 52, years: 3 }))).toBe("");
  });
});
