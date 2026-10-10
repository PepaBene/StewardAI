import { describe, expect, it } from "vitest";

import {
  claimTotals,
  EMPTY_WIRE_FILTERS,
  exposurePct,
  isWireFiltered,
  parseWireFilters,
  signedPct,
  wireFiltersToSearch,
  wireHeadline,
  wireQuery,
} from "./analysis";

describe("percent formatting", () => {
  it("shows exposure as a share of the portfolio and moves with their sign", () => {
    expect(exposurePct(0.0312)).toBe("3.1%");
    expect(exposurePct(null)).toBe("n/a");
    expect(signedPct(2)).toBe("+2.0%");
    expect(signedPct(-1.54)).toBe("-1.5%");
    expect(signedPct(0)).toBe("0.0%");
    expect(signedPct(undefined)).toBe("n/a");
  });
});

describe("wire filters", () => {
  it("round-trips through the address and ignores junk", () => {
    const filters = parseWireFilters(new URLSearchParams("region=jp&tier=2&linked=yes&q=yen&paywall=weird"));
    expect(filters).toMatchObject({ region: "JP", tier: "2", linked: "yes", q: "yen", paywall: "" });
    expect(parseWireFilters(new URLSearchParams(wireFiltersToSearch(filters)))).toEqual(filters);
    expect(wireFiltersToSearch(EMPTY_WIRE_FILTERS)).toBe("");
    expect(isWireFiltered(EMPTY_WIRE_FILTERS)).toBe(false);
    expect(isWireFiltered(filters)).toBe(true);
  });

  it("drops a tier outside 1 to 3", () => {
    expect(parseWireFilters(new URLSearchParams("tier=9")).tier).toBe("");
  });

  it("builds the API query with the linked toggle as a boolean", () => {
    const query = wireQuery({ ...EMPTY_WIRE_FILTERS, linked: "no", tier: "1", q: " Siemens " }, 40, 50);
    expect(query).toMatchObject({ linked: false, tier: 1, q: "Siemens", before_id: 40, limit: 50 });
    expect(query.region).toBeUndefined();
    expect(wireQuery(EMPTY_WIRE_FILTERS, null, 50).linked).toBeUndefined();
  });

  it("shows the translation only when it differs", () => {
    expect(wireHeadline({ headline: "円安が進む", title_en: "Yen weakens" })).toEqual({ text: "Yen weakens", translated: true });
    expect(wireHeadline({ headline: "Yen weakens", title_en: "Yen weakens" }).translated).toBe(false);
    expect(wireHeadline({ headline: "Yen weakens", title_en: null }).translated).toBe(false);
  });
});

describe("claim totals", () => {
  it("counts each verdict", () => {
    expect(claimTotals([{ verdict: "supported" }, { verdict: "unverified" }, { verdict: "supported" }])).toEqual({
      supported: 2,
      contradicted: 0,
      unverified: 1,
    });
  });
});
