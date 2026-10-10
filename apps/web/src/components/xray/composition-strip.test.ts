import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { XrayBucket } from "@/lib/xray/xray";

import { CompositionStrip } from "./composition-strip";

function bucket(key: string, weight: number, kind: XrayBucket["kind"] = "classified"): XrayBucket {
  return {
    key,
    label: key,
    kind,
    weight,
    value_base: weight * 1000,
    direct_weight: 0,
    fund_weight: weight,
    contributors: [],
    basis: {},
  };
}

const render = (buckets: XrayBucket[]) =>
  renderToStaticMarkup(createElement(CompositionStrip, { buckets, label: "Sector" }));

describe("CompositionStrip", () => {
  const html = render([
    bucket("Information Technology", 0.54),
    bucket("Consumer Staples", 0.3),
    bucket("__cash__", 0.1, "cash"),
    bucket("__unknown__", 0.06, "unknown_fund"),
  ]);

  it("hatches unknown weight and names every segment with its weight", () => {
    expect(html).toContain("hatch-unknown");
    expect(html).toContain("Information Technology");
    expect(html).toContain("54.0%");
    expect(html).toContain("6.0%");
    expect(html).toContain('role="img"');
    expect(html).toContain("Sector: Information Technology 54.0%");
  });

  it("sizes segments so the bar spans the whole portfolio", () => {
    const widths = [...html.matchAll(/style="width:([\d.]+)%"/g)].map((m) => Number(m[1]));
    expect(widths).toHaveLength(4);
    expect(widths.reduce((a, b) => a + b, 0)).toBeCloseTo(100, 5);
  });

  it("draws nothing for an empty view", () => {
    expect(render([])).toBe("");
  });
});
