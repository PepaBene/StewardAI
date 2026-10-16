import { describe, expect, it } from "vitest";

import { uniqueReasons } from "./reason-once";

describe("uniqueReasons", () => {
  it("drops blanks and repeats regardless of case and spacing", () => {
    expect(uniqueReasons(["Needs 250 days", " needs  250 days ", null, "", "Beta missing"])).toEqual(["Needs 250 days", "Beta missing"]);
  });
});
