import { describe, expect, it } from "vitest";

import { factProvenance, PROVENANCE } from "./provenance";

describe("provenance", () => {
  it("is a separate question from confidence: no tone, just label and description", () => {
    for (const p of Object.values(PROVENANCE)) {
      expect(p).not.toHaveProperty("tone");
    }
  });

  it("reads a fact's origin as verbatim or an owner correction", () => {
    expect(factProvenance({ origin: "extraction" })).toBe("verbatim");
    expect(factProvenance({ origin: "owner" })).toBe("owner_correction");
  });
});
