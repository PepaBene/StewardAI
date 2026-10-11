import { describe, expect, it } from "vitest";

import { minutesLabel, usdLabel } from "./ops";

describe("usdLabel", () => {
  it("keeps precision for tiny spend and rounds the rest to cents", () => {
    expect(usdLabel(0.0042)).toBe("$0.0042");
    expect(usdLabel(0)).toBe("$0.00");
    expect(usdLabel(3.456)).toBe("$3.46");
  });
});

describe("minutesLabel", () => {
  it("reads minutes and hours, and n/a when unknown", () => {
    expect(minutesLabel(45.2)).toBe("45 min");
    expect(minutesLabel(130)).toBe("2 h 10 min");
    expect(minutesLabel(120)).toBe("2 h");
    expect(minutesLabel(null)).toBe("n/a");
  });
});
