import { describe, expect, it } from "vitest";

import { basisLabel, basisSentence, lossWidth } from "./replay";

describe("replay helpers", () => {
  it("scales a loss to the track and ignores gains and gaps", () => {
    expect(lossWidth(-0.2, 0.4)).toBe(50);
    expect(lossWidth(-0.9, 0.4)).toBe(100);
    expect(lossWidth(0.1, 0.4)).toBe(0);
    expect(lossWidth(null, 0.4)).toBe(0);
    expect(lossWidth(-0.1, 0)).toBe(0);
    expect(lossWidth(Number.NaN, 0.4)).toBe(0);
  });

  it("describes the evidence and leaves out parts that are zero", () => {
    const text = basisSentence({ real_weight: 0.6, proxied_weight: 0.3, unavailable_weight: 0 });
    expect(text).toContain("own prices");
    expect(text).toContain("estimated");
    expect(text).not.toContain("not covered");
    expect(basisSentence({ real_weight: 0, proxied_weight: 0, unavailable_weight: 0 })).toBe("Nothing could be replayed.");
  });

  it("names each basis in plain words", () => {
    expect(basisLabel("real")).toBe("own prices");
    expect(basisLabel("proxied")).toBe("estimated");
    expect(basisLabel("unavailable")).toBe("not covered");
  });
});
