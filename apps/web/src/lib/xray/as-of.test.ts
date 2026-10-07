import { describe, expect, it } from "vitest";

import { asOfQuery, parseAsOf, withAsOf } from "./as-of";

describe("parseAsOf", () => {
  it("accepts a real calendar date and nothing else", () => {
    expect(parseAsOf("2026-03-31")).toBe("2026-03-31");
    expect(parseAsOf("2026-02-30")).toBeUndefined();
    expect(parseAsOf("31-03-2026")).toBeUndefined();
    expect(parseAsOf("")).toBeUndefined();
    expect(parseAsOf(null)).toBeUndefined();
  });
});

describe("withAsOf and asOfQuery", () => {
  it("carry the date only when there is one", () => {
    expect(withAsOf("/xray/overlap", "2026-03-31")).toBe("/xray/overlap?as_of=2026-03-31");
    expect(withAsOf("/xray/overlap", undefined)).toBe("/xray/overlap");
    expect(asOfQuery("2026-03-31")).toEqual({ as_of: "2026-03-31" });
    expect(asOfQuery(undefined)).toEqual({});
  });
});
