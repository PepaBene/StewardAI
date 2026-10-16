import { describe, expect, it } from "vitest";

import { kpiAnchorId, kpiHref } from "./kpi-anchor";

describe("kpi anchor", () => {
  it("builds a stable anchor id distinct from the KPI's own detail-panel id", () => {
    expect(kpiAnchorId("max-single-position")).toBe("kpi-row-max-single-position");
  });

  it("sanitizes characters that aren't valid in an id", () => {
    expect(kpiAnchorId("weird id!")).toBe("kpi-row-weird_id_");
  });

  it("links to the holding page with that anchor", () => {
    expect(kpiHref("AAPL_US_EQ", "max-single-position")).toBe("/companies/AAPL_US_EQ#kpi-row-max-single-position");
  });

  it("encodes a ticker that needs it", () => {
    expect(kpiHref("A B", "k")).toBe("/companies/A%20B#kpi-row-k");
  });
});
