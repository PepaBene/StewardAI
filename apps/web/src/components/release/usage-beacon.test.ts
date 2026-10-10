import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ usePathname: () => "/" }));
vi.mock("@/lib/api/client", () => ({ getBrowserApiClient: () => ({}) }));

import { USAGE_PATH } from "./usage-beacon";

describe("USAGE_PATH", () => {
  it("accepts mixed-case entity paths", () => {
    expect(USAGE_PATH.test("/companies/AAPL")).toBe(true);
    expect(USAGE_PATH.test("/funds/IE00B5BMR087")).toBe(true);
    expect(USAGE_PATH.test("/")).toBe(true);
    expect(USAGE_PATH.test("/companies/BRK.B")).toBe(true);
  });

  it("rejects query strings, spaces and overlong paths", () => {
    expect(USAGE_PATH.test("/funds?x=1")).toBe(false);
    expect(USAGE_PATH.test("/a b")).toBe(false);
    expect(USAGE_PATH.test(`/${"a".repeat(121)}`)).toBe(false);
  });
});
