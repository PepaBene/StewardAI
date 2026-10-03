import { describe, expect, it } from "vitest";

import { errorMessage } from "./error-message";

describe("errorMessage", () => {
  it("returns a string detail as is", () => {
    expect(errorMessage({ detail: "Not found" })).toBe("Not found");
  });

  it("flattens a 422 detail array instead of printing [object Object]", () => {
    const body = { detail: [{ loc: ["body", "weight"], msg: "Input should be less than or equal to 1" }, { loc: ["query", "as_of"], msg: "Value error, in the future" }] };
    expect(errorMessage(body)).toBe("weight: Input should be less than or equal to 1; as_of: in the future");
  });

  it("falls back when there is nothing readable", () => {
    expect(errorMessage(undefined, "Request failed")).toBe("Request failed");
    expect(errorMessage({ detail: 5 }, "x")).toBe("x");
    expect(errorMessage({ detail: [] }, "x")).toBe("x");
  });
});
