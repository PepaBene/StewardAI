import { describe, expect, it } from "vitest";

import { evidenceItems, fractionPct } from "./evidence-items";
import { parseNetworkSummary, parsePatentSummary } from "./parse";
import type { EvidenceSummary } from "./types";

const cov = { state: "covered", reason: "" };
const summaryWith = (over: Record<string, unknown>) =>
  ({
    positions: { control: { coverage: cov, stakes: [] }, institutional: { coverage: cov, pit_note: null, ownership: null }, funds: { coverage: cov, positions: { positions: [] } } },
    filed_notes: { coverage: cov, notes: [] },
    dependencies: { coverage: cov, dependencies: [], excluded_unconfirmed: 0 },
    ...over,
  }) as unknown as EvidenceSummary;

describe("F5-01 13F share of outstanding is a fraction", () => {
  it("0.0412 is printed as 4.12%, not 0.0412%", () => {
    expect(fractionPct(0.0412)).toBe("4.12%");
    expect(fractionPct("0.0412")).toBe("4.12%");
    expect(fractionPct(null)).toBeNull();
  });
  it("the ledger row for a 13F holder and the 13D/G stake keep their own units", () => {
    const s = summaryWith({
      positions: {
        control: { coverage: cov, stakes: [{ stake: { accession: "A1", schedule: "13D", issuer_name: "I", holders: [{ person_name: "P" }], stake_percent: "5.2", stake_basis: "class", form: "SC 13D", source_url: null }, superseded: false, source_passage: "p", passage_reason: null, known_at: null, coverage_reason: null }] },
        institutional: { coverage: cov, pit_note: null, ownership: { source: "13f", periods: [], top_holders: [{ cik: "1", accession: "B1", manager_name: "Vanguard", shares: 412345000, pct_of_shares_outstanding: 0.0412 }] } },
        funds: { coverage: cov, positions: { positions: [] } },
      },
    });
    const items = evidenceItems(s, "positions");
    expect(items.find((i) => i.key.startsWith("13f"))!.detail).toContain("4.12% of outstanding");
    expect(items.find((i) => i.key.startsWith("13f"))!.detail).not.toContain("0.0412%");
    expect(items.find((i) => i.key.startsWith("stake"))!.detail).toContain("5.2%");
  });
  it("a dependency's reported share fraction is shown as a percent", () => {
    const s = summaryWith({ dependencies: { coverage: cov, excluded_unconfirmed: 0, dependencies: [{ kind: "edge", edge_id: 1, relationship: "customer", counterparty_name: "C", anonymous_counterparty: false, direction: "issuer_sells_to", reported_share: "0.3500", share_basis: "issuer_revenue", fiscal_period: "FY25", evidence_tier: "primary", passage: "x", passage_reason: null, known_at: null, source_url: null, source: "sec" }] } });
    expect(evidenceItems(s, "dependencies")[0]!.detail).toContain("35.0% of the company's revenue");
  });
  it("an empty-string passage is 'no passage' with a reason", () => {
    const s = summaryWith({ filed_notes: { coverage: cov, notes: [{ fact_id: 1, revision: 1, concept: "c", label: null, period_end: "2025-12-31", fiscal_period: "FY", form: "10-K", accession: "a", passage: "  ", passage_reason: null, known_at: null, source_url: null, source: "sec" }] } });
    expect(evidenceItems(s, "filed-notes")[0]!.passageReason).toBe("No passage stored for this fact.");
  });
});

describe("F5-07 holding-page network and patent counts", () => {
  it("a family listed once per owner row counts once; attributed if any row is", () => {
    const p = parsePatentSummary({
      families: [
        { family_key: "F1", attributed: false, legal_status: "granted" },
        { family_key: "F1", attributed: true, legal_status: "granted" },
        { family_key: "F2", attributed: false, legal_status: "application" },
      ],
      trend: [],
    })!;
    expect(p.families).toBe(2);
    expect(p.attributed).toBe(1);
    expect(p.byLegalStatus).toEqual({ granted: 1, application: 1 });
  });
  it("rejected edges are not candidates and not in the denominator", () => {
    const n = parseNetworkSummary({ edges: [{ status: "confirmed" }, { status: "suggested" }, { status: "rejected" }, { status: "rejected" }], events: [], disclosures: [] })!;
    expect(n.live).toBe(2);
    expect(n.rejected).toBe(2);
    expect(n.candidates).toBe(1);
    expect(n.confirmed).toBe(1);
  });
});
