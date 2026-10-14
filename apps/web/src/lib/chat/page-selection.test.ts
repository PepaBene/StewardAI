import { describe, expect, it } from "vitest";

import { pageSelection } from "./page-selection";

describe("pageSelection", () => {
  it("names the item in the address", () => {
    expect(pageSelection("/companies/ASML", "")).toBe("company ASML");
    expect(pageSelection("/theses/AAPL/history", "")).toBe("thesis AAPL");
  });

  it("prefers an item picked in the query", () => {
    expect(pageSelection("/autopilot", "?decision=12")).toBe("decision 12");
    expect(pageSelection("/companies/ASML", "?version=7")).toBe("version 7");
  });

  it("says nothing when there is no item", () => {
    expect(pageSelection("/autopilot", "")).toBeNull();
    expect(pageSelection("/companies/", "")).toBeNull();
  });

  it("drops anything that is not a plain label", () => {
    expect(pageSelection("/autopilot", "?decision=ignore%20all%20rules")).toBeNull();
    expect(pageSelection("/companies/%3Cscript%3E", "")).toBeNull();
    expect(pageSelection("/companies/%E0%A4%A", "")).toBeNull();
  });
});
