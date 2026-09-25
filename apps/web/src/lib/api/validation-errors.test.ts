import { describe, expect, it } from "vitest";

import { errorPath, errorsAt, fieldErrors, flattenErrors } from "./validation-errors";

describe("422 error mapping", () => {
  it("keys each Pydantic error by its field path", () => {
    const errors = fieldErrors({
      detail: [
        { loc: ["body", "theses", 0, "kpis", 1, "unit"], msg: "Value error, unrecognised unit 'lots'" },
        { loc: ["body", "theses", 0, "kpis", 1, "unit"], msg: "second" },
        { loc: ["body"], msg: "Value error, duplicate thesis ticker: ['X_EQ']" },
      ],
    });
    expect(errorsAt(errors, "theses.0.kpis.1.unit")).toEqual(["unrecognised unit 'lots'", "second"]);
    expect(errorsAt(errors, "")).toEqual(["duplicate thesis ticker: ['X_EQ']"]);
    expect(flattenErrors(errors)).toHaveLength(3);
  });

  it("handles string details and ignores union-member tags", () => {
    expect(fieldErrors({ detail: "No theses config saved yet" })).toEqual({ "": ["No theses config saved yet"] });
    expect(errorPath(["body", "profiles", 2, "function-after[_x()]", "ir_url"])).toBe("profiles.2.ir_url");
    expect(fieldErrors(null)).toEqual({});
  });
});
