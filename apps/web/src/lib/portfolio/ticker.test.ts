import { describe, expect, it } from "vitest";

import { indexName, shortTicker } from "./ticker";

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

describe("indexName", () => {
  it("names known index symbols and keeps unknown ones", () => {
    expect(indexName("^spx")).toBe("S&P 500");
    expect(indexName("^SPX")).toBe("S&P 500");
    expect(indexName("^xyz")).toBe("^xyz");
  });
});
