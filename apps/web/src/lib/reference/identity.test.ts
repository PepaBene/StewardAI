import { describe, expect, it } from "vitest";

import type { ListingOut, RelationshipOut } from "@/lib/api/client";

import {
  decisionMessage,
  describeException,
  gleifHref,
  groupRelated,
  humanizeCode,
  listingState,
  parseWikidata,
  RELATIONSHIP_LABELS,
  safeHref,
  sortListings,
  sortParents,
  sourceLabel,
  tierLabel,
  disputeChoices,
  parseConflict,
} from "./identity";

const NOW = new Date("2026-09-25T12:00:00Z");

function listing(mic: string, extra: Partial<ListingOut> = {}): ListingOut {
  return {
    mic,
    full_name: "SAP SE",
    cfi: "ESVUFR",
    currency: "EUR",
    first_trade_at: "2001-01-02T00:00:00Z",
    terminated_at: null,
    status: "active",
    home: false,
    ...extra,
  };
}

function rel(id: number, type: RelationshipOut["type"], name: string, extra: Partial<RelationshipOut> = {}): RelationshipOut {
  return {
    id,
    type,
    object_name: name,
    object_lei: null,
    source: "gleif",
    sources: ["gleif"],
    source_ref: null,
    as_of: "2026-09-01",
    status: "confirmed",
    tier: 1,
    ...extra,
  };
}

describe("listings", () => {
  it("marks the home market and distinguishes terminated venues", () => {
    expect(listingState(listing("XETR", { home: true }), NOW)).toBe("home");
    expect(listingState(listing("XFRA"), NOW)).toBe("active");
    expect(listingState(listing("XBER", { terminated_at: "2024-06-30T00:00:00Z" }), NOW)).toBe("terminated");
    // A termination date in the future is still an active venue today.
    expect(listingState(listing("XMUN", { terminated_at: "2027-01-01T00:00:00Z" }), NOW)).toBe("active");
  });

  it("sorts home first, then active by MIC, then terminated (latest first)", () => {
    const sorted = sortListings(
      [
        listing("XBER", { terminated_at: "2020-01-01T00:00:00Z" }),
        listing("XFRA"),
        listing("XDUS", { terminated_at: "2024-01-01T00:00:00Z" }),
        listing("XETR", { home: true }),
        listing("TRQX"),
      ],
      NOW,
    );
    expect(sorted.map((l) => l.mic)).toEqual(["XETR", "TRQX", "XFRA", "XDUS", "XBER"]);
  });
});

describe("group structure", () => {
  it("labels GLEIF parents as consolidating parents, not owners", () => {
    expect(RELATIONSHIP_LABELS.parent).toBe("Consolidating parent (direct)");
    expect(RELATIONSHIP_LABELS.ultimate_parent).toBe("Ultimate consolidating parent");
    const sorted = sortParents([rel(2, "ultimate_parent", "Top"), rel(1, "parent", "Mid")]);
    expect(sorted.map((r) => r.id)).toEqual([1, 2]);
  });

  it("states GLEIF's reason when no parent is reported", () => {
    expect(
      describeException({ category: "DIRECT_ACCOUNTING_CONSOLIDATION_PARENT", reason: "NATURAL_PERSONS" }),
    ).toBe("No direct consolidating parent reported: natural persons.");
    expect(describeException({ category: "ULTIMATE_ACCOUNTING_CONSOLIDATION_PARENT", reason: "NO_KNOWN_PERSON" })).toBe(
      "No ultimate consolidating parent reported: no known person.",
    );
    expect(humanizeCode("NON_CONSOLIDATING")).toBe("Non consolidating");
    expect(humanizeCode("Free text")).toBe("Free text");
  });

  it("groups confirmed competitors, suppliers and customers", () => {
    const groups = groupRelated([
      rel(1, "customer", "Zeta"),
      rel(2, "competitor", "Oracle"),
      rel(3, "competitor", "Microsoft"),
    ]);
    expect(groups.map((g) => g.type)).toEqual(["competitor", "customer"]);
    expect(groups[0].items.map((r) => r.object_name)).toEqual(["Microsoft", "Oracle"]);
  });
});

describe("sources and links", () => {
  it("names sources and tiers", () => {
    expect(sourceLabel("wikidata")).toBe("Wikidata");
    expect(sourceLabel("gleif")).toBe("GLEIF");
    expect(sourceLabel("something")).toBe("something");
    expect(tierLabel(3)).toContain("Tier 3");
    expect(tierLabel(1)).toContain("Tier 1");
  });

  it("links an LEI to GLEIF and only renders http(s) links", () => {
    expect(gleifHref("529900D6BF99LW9R2E68")).toBe("https://search.gleif.org/#/record/529900D6BF99LW9R2E68");
    expect(safeHref("https://www.wikidata.org/wiki/Q552581")).toBe("https://www.wikidata.org/wiki/Q552581");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref(null)).toBeNull();
  });

  it("reads the Wikidata summary defensively (a row written by an older backend may lack fields)", () => {
    expect(parseWikidata(null)).toBeNull();
    expect(parseWikidata({ industry: ["x"] } as never)).toBeNull();
    expect(
      parseWikidata({
        qid: "Q552581",
        industry: ["software industry", 3 as unknown as string],
        products: ["SAP ERP"],
        ceo: ["Christian Klein"],
        chair: [],
        website: "https://www.sap.com",
        part_of: ["DAX"],
        fetched_at: "2026-09-20T04:30:00Z",
      }),
    ).toEqual({
      qid: "Q552581",
      industry: ["software industry"],
      products: ["SAP ERP"],
      ceo: ["Christian Klein"],
      chair: [],
      website: "https://www.sap.com",
      partOf: ["DAX"],
      fetchedAt: "2026-09-20T04:30:00Z",
    });
  });
});

describe("decision messages", () => {
  const suggestion = rel(7, "competitor", "Oracle", { status: "confirmed", source: "wikidata", tier: 3 });

  it("names the new holding-profiles version after confirming a competitor", () => {
    expect(decisionMessage("confirm", { relationship: suggestion, holding_profiles_version_id: 42 })).toBe(
      "Confirmed Oracle as competitor. Saved as holding-profiles version #42; the next news run matches on it.",
    );
  });

  it("confirms structure without a profile version, and explains a rejection", () => {
    expect(decisionMessage("confirm", { relationship: rel(8, "subsidiary", "SAP America"), holding_profiles_version_id: null })).toBe(
      "Confirmed SAP America as subsidiary.",
    );
    expect(
      decisionMessage("reject", { relationship: { ...suggestion, status: "rejected" }, holding_profiles_version_id: null }),
    ).toBe("Rejected Oracle as competitor. It will not be suggested again.");
  });
});

describe("disputed relationships", () => {
  it("reads the contradicting claim", () => {
    expect(
      parseConflict({ source: "gleif", tier: 1, type: "parent", object_key: "lei:X", object_name: " Parent AG ", source_ref: "https://api.gleif.org/x" }),
    ).toEqual({ source: "gleif", tier: 1, type: "parent", objectName: "Parent AG", sourceRef: "https://api.gleif.org/x" });
    expect(parseConflict({ object_name: "Parent AG", type: "weird" })).toEqual({
      source: "unknown",
      tier: null,
      type: null,
      objectName: "Parent AG",
      sourceRef: null,
    });
    expect(parseConflict(null)).toBeNull();
    expect(parseConflict({ source: "gleif" })).toBeNull();
  });

  it("says what each decision does", () => {
    const conflict = parseConflict({ source: "gleif", tier: 1, type: "parent", object_name: "Parent AG" });
    const choices = disputeChoices({ object_name: "Other Holding", type: "parent" }, conflict);
    expect(choices.confirm).toBe("Confirm Other Holding as consolidating parent (direct): your decision wins over Parent AG (GLEIF).");
    expect(choices.reject).toMatch(/^Reject Other Holding .*: Parent AG \(GLEIF\) stands/);
    expect(disputeChoices({ object_name: "X", type: "parent" }, null).reject).toMatch(/the other source's version stands/);
  });
});
