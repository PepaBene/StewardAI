import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ZeroCostGate } from "@/lib/source-coverage/types";

import { GateSummary } from "./source-coverage-panel";

const gate = (over: Partial<ZeroCostGate> = {}): ZeroCostGate => ({
  passed: true, checked: 5, freeAvailable: 3, disabledOk: 1, gatedUnverified: 1, violationCount: 0, warningCount: 0, warningsByCode: {}, findings: [], ...over,
});

describe("GateSummary", () => {
  it("F1-01: an audit of zero manifest endpoints reads as not verified, not passed", () => {
    const html = renderToStaticMarkup(createElement(GateSummary, { gate: gate({ checked: 0, freeAvailable: 0, disabledOk: 0, gatedUnverified: 0 }) }));
    expect(html).toContain("not verified");
    expect(html).not.toContain("passed");
  });
  it("still says passed for a real audit and failed with the violation count", () => {
    expect(renderToStaticMarkup(createElement(GateSummary, { gate: gate() }))).toContain("passed");
    expect(renderToStaticMarkup(createElement(GateSummary, { gate: gate({ passed: false, violationCount: 2 }) }))).toContain("failed (2 violations)");
  });
});
