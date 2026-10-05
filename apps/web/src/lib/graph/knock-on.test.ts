import { describe, expect, it } from "vitest";

import { directionLabel, knockOnSankey, linkCounts, parseKnockOn } from "./knock-on";

const payload = {
  known_as_of: null,
  limitations: ["a"],
  links: [
    { ticker: "AAA", counterparty_ticker: "BBB", relationship: "customer", direction: "issuer_sells_to", reported_share: "0.12", share_basis: "issuer revenue" },
    { ticker: "CCC", counterparty_ticker: "AAA", relationship: "supplier", direction: "issuer_buys_from", reported_share: null },
    { ticker: "DDD", counterparty_ticker: "EEE", relationship: "customer", direction: "both" },
    { ticker: null, counterparty_ticker: "X" },
  ],
};

describe("knock-on", () => {
  const k = parseKnockOn(payload)!;
  it("orients links and keeps missing shares null", () => {
    expect(k.links).toHaveLength(3);
    expect(k.links[0]).toMatchObject({ supplier: "AAA", customer: "BBB", reportedShare: 0.12 });
    expect(k.links[1]).toMatchObject({ supplier: "AAA", customer: "CCC", reportedShare: null });
    expect(k.links[2].supplier).toBeNull();
    expect(directionLabel(k.links[2])).toContain("direction unclear");
    expect(parseKnockOn(null)).toBeNull();
  });
  it("keeps unclear links out of the diagram and counts both roles", () => {
    const s = knockOnSankey(k.links);
    expect(s.unclear).toBe(1);
    expect(s.nodes.map((n) => n.id).sort()).toEqual(["c:BBB", "c:CCC", "s:AAA"]);
    const a = linkCounts(k.links).find((r) => r.ticker === "AAA");
    expect(a).toEqual({ ticker: "AAA", asSupplier: 2, asCustomer: 0 });
  });
});
