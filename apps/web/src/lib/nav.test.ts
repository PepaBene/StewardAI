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

describe("phase 3 pages", () => {
  it("marks News on the disputed stories, Reports on the impact notes and Activity on a job run", () => {
    expect(isSectionActive("/news", "/news/disputed")).toBe(true);
    expect(isSectionActive("/reports", "/escalations")).toBe(true);
    expect(isSectionActive("/reports", "/escalations/12")).toBe(true);
    expect(isSectionActive("/news", "/escalations")).toBe(false);
    expect(isSectionActive("/activity", "/jobs/42")).toBe(true);
    expect(isSectionActive("/activity", "/jobsx")).toBe(false);
  });
});
