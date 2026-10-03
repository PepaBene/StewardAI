import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ConcentrationPayload, NetworkDisclosure } from "@/lib/network/types";
import { GapsList } from "@/components/holding/gaps-list";
import { NetworkPatentsCard } from "@/components/holding/network-patents-card";
import { EvidenceLedger } from "@/components/holding/evidence-ledger";
import type { EvidenceSummary } from "@/lib/holding-data/types";

import { ConcentrationPanel } from "./concentration-panel";
import { DecisionControl } from "./decision-control";
import { IdentityPanel } from "./identity-panel";
import { ReviewPanel } from "./review-queue";
import { ResearchPanel } from "./thesis-links-panel";
import { PatentMatrix } from "./patent-matrix";

const html = (el: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(el);
const noop = () => undefined;
const decide = () => Promise.resolve({ ok: true as const, message: "ok" });

describe("F5-04 concentration panel text", () => {
  const data = (p: Partial<ConcentrationPayload["periods"][number]>): ConcentrationPayload => ({
    ticker: "T", limitations: [],
    periods: [{ fiscalPeriod: "FY2025", relationship: "customer", shareBasis: "issuer_revenue", namedCount: 2, anonymousCount: 1, namedShareSum: 0.5, anonymousShareSum: 0, reportedShareSum: 0.5, missingShareCount: 1, namedMissingShareCount: null, anonymousMissingShareCount: null, namedReportedShareSum: null, anonymousReportedShareSum: null, totalReportedShareSum: null, namedAtLeast: null, anonymousAtLeast: null, totalAtLeast: null, largestShare: 0.3, shareExceedsTotal: false, openContradictions: 0, ...p }],
  });
  it("never prints an unknown anonymous share as 0.0%", () => {
    const out = html(createElement(ConcentrationPanel, { data: data({}), asOfDate: null }));
    expect(out).not.toMatch(/(?<![\d.])0\.0%/);
    expect(out).toContain("at least 50.0%");
    expect(out).toContain("not reported");
  });
  it("the takeaway does not invent 0.0% when no share is reported", () => {
    const out = html(createElement(ConcentrationPanel, { data: data({ largestShare: null, namedShareSum: 0, namedCount: 1, anonymousCount: 0, missingShareCount: 1 }), asOfDate: null }));
    expect(out).toContain("no counterparty reported a share");
    expect(out).not.toMatch(/(?<![\d.])0\.0%/);
  });
});

describe("per-side concentration panel", () => {
  it("shows exact named share and a floor only for the anonymous side", () => {
    const out = html(createElement(ConcentrationPanel, {
      asOfDate: null,
      data: { ticker: "T", limitations: [], periods: [{ fiscalPeriod: "FY2025", relationship: "customer", shareBasis: "issuer_revenue", namedCount: 2, anonymousCount: 1, namedShareSum: 0.5, anonymousShareSum: 0.1, reportedShareSum: 0.6, missingShareCount: 1, namedMissingShareCount: 0, anonymousMissingShareCount: 1, namedReportedShareSum: 0.5, anonymousReportedShareSum: 0.1, totalReportedShareSum: 0.6, namedAtLeast: false, anonymousAtLeast: true, totalAtLeast: true, largestShare: 0.3, shareExceedsTotal: false, openContradictions: 0 }] },
    }));
    expect(out).toContain("Named 50.0%");
    expect(out).toContain("anonymous at least 10.0%");
    expect(out).not.toContain("at least 50.0%");
  });
});

describe("F5-05 gaps panel", () => {
  it("does not say 'no gap' while loading or when a request failed", () => {
    expect(html(createElement(GapsList, { gaps: [], complete: false, loading: true }))).not.toContain("reports no gap");
    const failed = html(createElement(GapsList, { gaps: [], complete: false, loading: false }));
    expect(failed).not.toContain("reports no gap");
    expect(failed).toContain("could not be fully read");
    expect(html(createElement(GapsList, { gaps: [], complete: true }))).toContain("reports no gap");
  });
});

describe("F5-07 holding card state", () => {
  it("'verified' is not claimed for patents with 0 attributed families", () => {
    const out = html(createElement(NetworkPatentsCard, { network: null, patents: { families: 3, attributed: 0, byLegalStatus: {}, latestKnownAt: null, trendRows: null }, ticker: "X", asOf: null, knownAsOf: null }));
    expect(out).toContain("none has a dated ownership path");
    expect(out).not.toContain("Verified");
  });
  it("rejected edges leave the denominator", () => {
    const out = html(createElement(NetworkPatentsCard, { network: { edges: 4, live: 2, rejected: 2, byStatus: { confirmed: 1, suggested: 1, rejected: 2 }, confirmed: 1, candidates: 1, events: 0, disclosures: 0, limitations: [] }, patents: null, ticker: "X", asOf: null, knownAsOf: null }));
    expect(out).toContain("1 of 2 graph edges are confirmed; 1 are candidates");
  });
});

describe("F5-08 identity panel as-of", () => {
  const identity = { legal_entity: null, parents: [], subsidiaries: [], owners: [], subsidiary_count: 0, parent_exceptions: [], isin: null, identity_error: null } as never;
  it("is labelled as the current structure and not stamped with the picked date", () => {
    const out = html(createElement(IdentityPanel, { identity, asOfParam: "2024-03-31T23:59:59Z", asOfDate: "2024-03-31" }));
    expect(out).toContain("Current structure, not as of");
    expect(out).not.toContain("As of 2024-03-31");
  });
});

describe("F5-09 review panel and research panel degrade visibly", () => {
  const d: NetworkDisclosure[] = [];
  it("a failed review queue is not shown as empty", () => {
    const out = html(createElement(ReviewPanel, { ticker: "T", disclosures: d, queue: null, queueStatus: "unavailable", queueReason: "HTTP 500", asOfDate: null, onChanged: noop }));
    expect(out).toContain("could not be loaded");
    expect(out).not.toContain("No relationship passage is stored for this company");
  });
  it("failed reconciliations are not 'None open.'", () => {
    const out = html(createElement(ResearchPanel, { observations: [], reconciliations: [], reconciliationsStatus: "unavailable", asOfDate: null }));
    expect(out).toContain("could not be loaded");
    expect(out).not.toContain("None open.");
  });
  it("F5-16: only rejected passages is not 'verified'", () => {
    const rejected = { id: 1, relationship: "customer", direction: "issuer_sells_to", counterpartyKey: "c", counterpartyName: "C", anonymous: false, reportedShare: null, shareBasis: null, fiscalPeriod: null, validFrom: null, validTo: null, evidenceId: null, passage: "p", sourceUrl: null, source: "s", status: "rejected", decisionReason: null, knownAt: null } as NetworkDisclosure;
    const out = html(createElement(ReviewPanel, { ticker: "T", disclosures: [rejected], queue: null, asOfDate: null, onChanged: noop }));
    expect(out).not.toContain("Verified");
  });
  it("F7-... one-click filing read now needs arming: no request button on load", () => {
    const hit = { id: 5, ticker: "T", filerName: "F", form: "10-K", filedAt: null, url: null, reviewState: "pending", reviewNote: null, attempts: null, disclosureId: null };
    const out = html(createElement(ReviewPanel, { ticker: "T", disclosures: [], queue: { filingHits: [hit], suggested: [], contradictions: [] }, queueStatus: "ok", asOfDate: null, onChanged: noop }));
    expect(out).toContain("Read passages…");
    expect(out).not.toContain("Confirm: read filing");
  });
});

describe("F5-11 decision control by status", () => {
  const render = (props: Record<string, unknown>) => html(createElement(DecisionControl, { onDecide: decide, onDone: noop, status: "suggested", ...props } as never));
  it("a historical as-of view is read-only", () => {
    const out = render({ readOnly: true });
    expect(out).toContain("Read-only");
    expect(out).not.toContain("Confirm as fact");
  });
  it("an unrecognised status offers no confirm", () => {
    const out = render({ status: "unrecognised" });
    expect(out).not.toContain("Confirm as fact");
    expect(out).toContain("not one this screen recognises");
  });
  it("a legacy confirmed edge offers no reject", () => {
    const out = render({ status: "confirmed", supportsAll: false, needsReason: false });
    expect(out).not.toContain(">Reject<");
  });
  it("a suggested edge still offers confirm, reject and dispute", () => {
    const out = render({});
    expect(out).toContain("Confirm as fact");
    expect(out).toContain("Reject");
    expect(out).toContain("Mark disputed");
  });
});

describe("F5-10 the edge detail is keyed per edge", () => {
  it("EdgeDetail is rendered with key={chosen.id} so a typed reason cannot carry to another edge", () => {
    const src = readFileSync(new URL("./edge-ledger.tsx", import.meta.url), "utf8");
    expect(src).toMatch(/<EdgeDetail\s+key=\{chosen\.id\}/);
  });
});

describe("F5-06 patent matrix legend", () => {
  it("unknown citation counts are labelled, not 'no citations'", () => {
    const p = { familyKey: "F", priorityDate: "2020-01-01", firstPublicationDate: null, title: "t", cpcCodes: ["H01L21/02"], jurisdictions: [], citationsForward: null, citationsBackward: null, legalStatus: "granted", source: "epo_ops", sourceUrl: null, knownAt: null, applicant: null, ownershipConfidence: null, ownershipBasis: null, ownershipValidFrom: null, ownershipValidTo: null, evidenceId: null, attributed: true };
    const out = html(createElement(PatentMatrix, { patents: [p], trends: null, asOfDate: null, knownAt: null, limitations: [] }));
    expect(out).toContain("citation counts unknown");
    expect(out).toContain("never as zero");
  });
});

describe("F5-17 evidence ledger with a blank passage", () => {
  it("shows a reason, not 'No passage: '", () => {
    const cov = { state: "covered", reason: "" };
    const s = { known_as_of: "2026-01-01T00:00:00Z", filed_notes: { coverage: cov, notes: [{ fact_id: 1, revision: 1, concept: "c", label: null, period_end: "2025-12-31", fiscal_period: "FY", form: "10-K", accession: "a", passage: "", passage_reason: null, known_at: null, source_url: null, source: "sec" }] }, positions: { control: { coverage: cov, stakes: [] }, institutional: { coverage: cov, pit_note: null, ownership: null }, funds: { coverage: cov, positions: { positions: [] } } }, insider_activity: { coverage: cov, net_discretionary_derived_from: [] }, dependencies: { coverage: cov, dependencies: [], excluded_unconfirmed: 0 }, deals: { coverage: cov, deals: [] }, consensus: { coverage: cov, series: [] } } as unknown as EvidenceSummary;
    expect(html(createElement(EvidenceLedger, { summary: s }))).toContain("No passage: No passage stored for this fact.");
  });
});
