import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { IrRelease } from "@/lib/financials/types";

import { IrPanel } from "./ir-panel";
import { barGeometry } from "./peer-table";
import { failureLine, ProbePanel } from "./probe-panel";

describe("F4-07 / F7-1 probe panel", () => {
  it("names the ticker in its introduction and never renders a pending confirm on first paint", () => {
    const html = renderToStaticMarkup(h(ProbePanel, { ticker: "MSFT", onDone: () => undefined }));
    expect(html).toContain("MSFT");
    expect(html).not.toContain("Spend one free request");
  });
  it("F7-6: a 422 detail array is readable, not [object Object]", () => {
    const line = failureLine({ detail: [{ loc: ["path", "ticker"], msg: "string does not match pattern", type: "value_error" }] });
    expect(line.join(" ")).not.toContain("[object Object]");
    expect(line.join(" ")).toContain("string does not match pattern");
    expect(failureLine({ detail: "No API key configured" })).toEqual(["No API key configured"]);
  });
});

describe("F4-11 peer bars", () => {
  it("a negative value extends left of the zero line, a positive one right, with equal length for equal magnitude", () => {
    const g = barGeometry([-0.2, 0.2]);
    expect(g.bars[0]!.left).toBeCloseTo(0);
    expect(g.bars[0]!.left + g.bars[0]!.width).toBeCloseTo(g.zero);
    expect(g.bars[1]!.left).toBeCloseTo(g.zero);
    expect(g.bars[1]!.width).toBeCloseTo(g.bars[0]!.width);
    expect(barGeometry([null, 0.1]).bars[0]).toBeNull();
  });
});

describe("F4-15 / F7-10 IR links", () => {
  it("renders a javascript: URL as text, not a link", () => {
    const release: IrRelease = { id: 1, releaseType: "guidance", sourceUrl: "javascript:alert(1)", publishedAt: null, knownAt: null, analystCount: null, basis: null, status: null };
    const html = renderToStaticMarkup(h(IrPanel, { releases: [release], sources: [] }));
    expect(html).not.toContain('href="javascript');
    const ok = renderToStaticMarkup(h(IrPanel, { releases: [{ ...release, sourceUrl: "https://example.com/r" }], sources: [] }));
    expect(ok).toContain('href="https://example.com/r"');
  });
});
