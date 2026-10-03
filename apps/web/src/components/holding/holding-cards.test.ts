import { createElement as h } from "react";
import { renderToStaticMarkup as render } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ClaimKnownAsOf } from "./claim-known-as-of";

import type { CoverageEntry, Disagreement, EvidenceSummary, FinancialHistory, Gap } from "@/lib/holding-data/types";
import type { HoldingMacroContext } from "@/lib/macro/types";

import { EvidenceLedger } from "./evidence-ledger";
import { ExposureCard } from "./exposure-card";
import { FinancialHistoryCard, latestFacts } from "./financial-history-card";
import { GapsList } from "./gaps-list";
import { HoldingAsOfBar } from "./holding-as-of-bar";
import { NetworkPatentsCard } from "./network-patents-card";
import { OwnershipSummaryCard } from "./ownership-summary-card";
import { SourceDisagreementsCard } from "./source-disagreements-card";

const cov = (state: "covered" | "partial" | "not_covered", reason = "why") => ({ state, reason });
const emptyHistory: FinancialHistory = { total_rows: 0, returned_rows: 0, statements: {}, earliest_period: null, latest_period: null, provenance: {}, facts: [] };
const fact = (id: number, period: string, over: Record<string, unknown> = {}) => ({ id, concept: "Revenues", label: null, statement: "income", period_start: null, period_end: period, fiscal_period: "FY", basis: "reported", value: "10", unit: "USD", currency: "USD", dimensions: null, source: "sec_xbrl", provenance: "primary", source_url: "https://sec.gov/x", known_at: "2026-02-01T00:00:00Z", revision: 1, has_text_block: false, evidence_id: null, ...over }) as FinancialHistory["facts"][number];

const summary = (over: Partial<Record<string, unknown>> = {}) =>
  ({
    ticker: "X", known_as_of: "2026-01-01T00:00:00Z", gaps: [],
    filed_notes: { coverage: cov("not_covered", "No notes filed"), notes: [] },
    positions: { not_additive: "Channels are not additive.", control: { channel: "beneficial_ownership_13dg", coverage: cov("not_covered"), stakes: [] }, institutional: { channel: "institutional_13f", coverage: cov("partial"), ownership: null, pit_note: null }, funds: { channel: "fund_positions_nport", coverage: cov("covered"), positions: { positions: [] } } },
    insider_activity: { coverage: cov("not_covered", "No Form 4"), known_by: "2026-01-01", activity: { net_discretionary: { since: "2025-01-01", until: "2026-01-01", net_shares: "0", buy_rows: 0, sell_rows: 0, value_complete: false, plan_status_unknown_rows: 2 } }, net_discretionary_derived_from: [] },
    dependencies: { coverage: cov("covered"), dependencies: [], excluded_unconfirmed: 3 },
    deals: { coverage: cov("covered"), deals: [] },
    consensus: { coverage: cov("covered"), series: [], issuer_published: [] },
    ...over,
  }) as unknown as EvidenceSummary;

describe("holding data cards render in every state", () => {
  it("financial history: empty is unavailable, not zero; populated shows source and known-at", () => {
    expect(render(h(FinancialHistoryCard, { history: emptyHistory, ticker: "X" }))).toContain("No financial statement facts");
    const html = render(h(FinancialHistoryCard, { history: { ...emptyHistory, total_rows: 2, returned_rows: 1, statements: { income: 2 }, provenance: { primary: 1, vendor: 1 }, earliest_period: "2024-12-31", latest_period: "2025-12-31", facts: [fact(1, "2025-12-31")] }, ticker: "X" }));
    expect(html).toContain("Known at");
    expect(html).toContain("1 of 2");
    expect(html).toContain("/financials?ticker=X");
  });
  it("latestFacts orders latest period first", () => {
    expect(latestFacts([fact(1, "2023-12-31"), fact(2, "2025-12-31")]).map((f) => f.id)).toEqual([2, 1]);
  });
  it("exposure: not loaded, unmapped and mapped states", () => {
    expect(render(h(ExposureCard, { macro: null, trade: [], ticker: "X" }))).toContain("not available");
    const unmapped = { asOf: "2026-01-01", observations: [], coverage: [], effects: [], comparisons: [], exposureUnknown: true, seriesEffects: [] } as HoldingMacroContext;
    expect(render(h(ExposureCard, { macro: unmapped, trade: [], ticker: "X" }))).toContain("No country or product exposure mapping");
    const mapped = { ...unmapped, exposureUnknown: false, effects: [{ ticker: "X", issuerId: 1, segment: "sales", basis: "reported", weight: 0.4, geography: "DE", hsCode: null, unknownWeight: 0.6, explanation: "", dimension: "geography", relatedSeries: [] }] } as unknown as HoldingMacroContext;
    const html = render(h(ExposureCard, { macro: mapped, trade: [], ticker: "X" }));
    expect(html).toContain("unmapped");
    expect(html).toContain("no product (HS) exposure".replace("no", "No"));
  });
  it("network and patents: unreadable payloads are unavailable, never zero", () => {
    expect(render(h(NetworkPatentsCard, { network: null, patents: null, ticker: "X", asOf: null, knownAsOf: null }))).toContain("could be read");
    const html = render(h(NetworkPatentsCard, { network: { edges: 3, live: 3, rejected: 0, byStatus: { confirmed: 1, suggested: 2 }, confirmed: 1, candidates: 2, events: 0, disclosures: 0, limitations: [] }, patents: { families: 0, attributed: 0, byLegalStatus: {}, latestKnownAt: null, trendRows: null }, ticker: "X", asOf: null, knownAsOf: null }));
    expect(html).toContain("1 of 3 graph edges are confirmed");
    expect(html).toContain("/holdings/X/network");
  });
  it("disagreements and coverage ledger", () => {
    const d = { conflict_id: 1, metric: "Revenues", status: "open", tolerance: null, difference: "2", preferred_source: "sec_xbrl", preferred_provenance: "primary", preferred_value: "100", preferred_known_at: null, alternative_source: "fmp", alternative_provenance: "vendor", alternative_value: "102", alternative_known_at: "2026-01-02T00:00:00Z", period_end: "2025-12-31", resolution: null } as Disagreement;
    const c = { provider: "fmp", dataset: "statements", entity_key: "X", state: "not_covered", latest_period: null, expected_periods: null, available_periods: null, checked_at: "2026-01-01T00:00:00Z", reason: "free tier" } as CoverageEntry;
    const html = render(h(SourceDisagreementsCard, { disagreements: [d], coverage: [c], asOf: null }));
    expect(html).toContain("Primary");
    expect(html).toContain("Vendor");
    expect(html).toContain("tolerance not stored");
    expect(html).toContain("no denominator");
    expect(render(h(SourceDisagreementsCard, { disagreements: [], coverage: [], asOf: null }))).toContain("No stored figure");
  });
  it("ownership summary keeps channels separate with their reasons", () => {
    const html = render(h(OwnershipSummaryCard, { summary: summary(), ticker: "BRK.B" }));
    expect(html).toContain("not additive");
    expect(html).toContain("/xray?tab=ownership&amp;ticker=BRK.B");
    expect(html).toContain("1 of 3 position channels");
  });
  it("evidence ledger: gap wording for an empty domain", () => {
    const html = render(h(EvidenceLedger, { summary: summary() }));
    expect(html).toContain("This is a gap, not a zero");
    expect(html).toContain("No notes filed");
  });
  it("gaps list: empty and populated", () => {
    expect(render(h(GapsList, { gaps: [] }))).toContain("reports no gap");
    const g: Gap[] = [{ domain: "patents", state: "missing", reason: "No family" }, { domain: "x", state: "stale", reason: "old" }];
    const html = render(h(GapsList, { gaps: g }));
    expect(html).toContain("No family");
    expect(html).toContain("2 gaps");
  });
  it("as-of bar warns on a future date and states the cutoff", () => {
    const html = render(h(HoldingAsOfBar, { value: "2099-01-01", onChange: () => {}, today: "2026-10-03", knownAsOf: "2026-01-01T00:00:00Z" }));
    expect(html).toContain("in the future");
    expect(html).toContain("2026-01-01");
    expect(render(h(HoldingAsOfBar, { value: "", onChange: () => {}, today: "2026-10-03", knownAsOf: null }))).toContain("Waiting for the API");
  });
});

describe("ClaimKnownAsOf", () => {
  const sources = new Map([
    ["S1", { source: { as_of: "2025-03-01" } }],
    ["S2", { source: { as_of: null } }],
  ]);
  const r = (ids: string[] | undefined) => render(h(ClaimKnownAsOf, { ids, sources }));
  it("shows the latest cited date, counts undated citations, and is empty without citations", () => {
    expect(r(["S1"])).toContain("Source data dated 2025-03-01");
    expect(r(["S1", "S2"])).toContain("+1 undated");
    expect(r(["S2"])).toContain("Cited sources undated");
    expect(r([])).toBe("");
    expect(r(undefined)).toBe("");
  });
});
