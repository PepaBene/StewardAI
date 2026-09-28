import { describe, expect, it } from "vitest";

import { shortTicker } from "./ticker";

describe("shortTicker", () => {
  it("drops the venue suffix", () => {
    expect(shortTicker("NVDA_US_EQ")).toBe("NVDA");
    expect(shortTicker("VWCEd_EQ")).toBe("VWCE");
    expect(shortTicker("ASMLa_EQ")).toBe("ASML");
    expect(shortTicker("NOVOb_EQ")).toBe("NOVO B");
    expect(shortTicker("CEZ_EQ")).toBe("CEZ");
    expect(shortTicker("Cash")).toBe("Cash");
  });
});
