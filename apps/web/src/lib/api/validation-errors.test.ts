import { describe, expect, it } from "vitest";

import { errorPath, errorsAt, fieldErrors, flattenErrors, foldChildErrors } from "./validation-errors";

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

describe("keyed field errors", () => {
  it("folds a keyed field's child errors into the field and keeps the rest", () => {
    const folded = foldChildErrors(
      {
        macro_surprise_bands: ["bad"],
        "macro_surprise_bands.hicp": ["Input should be greater than or equal to 0"],
        price_move_threshold_pct: ["too big"],
      },
      "macro_surprise_bands",
    );
    expect(folded).toEqual({
      macro_surprise_bands: ["bad", "hicp: Input should be greater than or equal to 0"],
      price_move_threshold_pct: ["too big"],
    });
    expect(foldChildErrors({ other: ["x"] }, "macro_surprise_bands")).toEqual({ other: ["x"] });
  });
});
