import { describe, expect, it } from "vitest";

import { isSectionActive } from "./nav";

describe("section highlighting", () => {
  it("marks Holdings active on its pages, the thesis board, the review queue and the transactions view", () => {
    expect(isSectionActive("/holdings", "/theses")).toBe(true);
    expect(isSectionActive("/holdings", "/review")).toBe(true);
    expect(isSectionActive("/config", "/theses")).toBe(false);
    expect(isSectionActive("/holdings", "/holdings")).toBe(true);
    expect(isSectionActive("/holdings", "/holdings/AAPL_US_EQ")).toBe(true);
    expect(isSectionActive("/holdings", "/transactions")).toBe(true);
    expect(isSectionActive("/holdings", "/transactionsx")).toBe(false);
    expect(isSectionActive("/", "/transactions")).toBe(false);
    expect(isSectionActive("/", "/")).toBe(true);
    expect(isSectionActive("/config", "/config/history/strategy")).toBe(true);
  });
});
