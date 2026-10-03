import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ResourceState } from "@/lib/hooks/use-api-resource";
import type { BusinessNetwork, ConcentrationPayload, DealsPayload, NetworkEdge, Parsed, PatentFamily } from "@/lib/network/types";

import { RowStatusBadge } from "./badges";
import { ChangesList } from "./changes-list";
import { ConcentrationPanel, KnockOnPanel } from "./concentration-panel";
import { DealsTimeline } from "./deals-timeline";
import { EdgeLedger } from "./edge-ledger";
import { LeadershipPanel, PeerRanking } from "./peer-leadership";
import { PatentMatrix } from "./patent-matrix";
import { ResourceBoundary } from "./resource-boundary";
import { KpiLinksPanel, ResearchPanel } from "./thesis-links-panel";

const edge = (over: Partial<NetworkEdge> = {}): NetworkEdge => ({
  id: "1", rowId: 1, legacy: false, subjectKey: "T", objectKey: "x", objectName: "Acme Corp", edgeType: "partner", direction: "symmetric",
  status: "suggested", validFrom: "2024-01-01", validTo: null, tier: "vendor", source: "diffbot", sourceUrl: "https://example.com/a", evidenceId: null,
  passage: "Acme and the company announced a partnership.", knownAt: "2026-02-01T00:00:00Z", decisionReason: null, detail: {}, ...over,
});

const patent = (over: Partial<PatentFamily> = {}): PatentFamily => ({
  familyKey: "F1", priorityDate: "2020-03-01", firstPublicationDate: null, title: "Widget", cpcCodes: ["H01L21/02"], jurisdictions: ["EP"],
  citationsForward: 4, citationsBackward: null, legalStatus: "granted", source: "epo_ops", sourceUrl: null, knownAt: "2026-01-01T00:00:00Z",
  applicant: "A", ownershipConfidence: 0.9, ownershipBasis: "direct", ownershipValidFrom: null, ownershipValidTo: null, evidenceId: null, attributed: true, ...over,
});

const net = (over: Partial<BusinessNetwork> = {}): BusinessNetwork => ({ ticker: "T", knownAsOf: null, edges: [], events: [], disclosures: [], patents: [], limitations: [], ...over });
const noop = () => undefined;
const html = (el: Parameters<typeof renderToStaticMarkup>[0]) => renderToStaticMarkup(el);

describe("status badges", () => {
  it("labels every status in words and keeps suggested distinct from confirmed", () => {
    const suggested = html(createElement(RowStatusBadge, { status: "suggested" }));
    const confirmed = html(createElement(RowStatusBadge, { status: "confirmed" }));
    expect(suggested).toContain("Suggested");
    expect(suggested).toContain("border-dashed");
    expect(confirmed).toContain("Confirmed");
    expect(confirmed).not.toContain("border-dashed");
    expect(html(createElement(RowStatusBadge, { status: "unrecognised" }))).toContain("Unrecognised");
  });
});

describe("EdgeLedger", () => {
  const render = (edges: NetworkEdge[]) =>
    html(createElement(EdgeLedger, { ticker: "T", edges, asOfDate: null, limitations: ["Suggested and disputed edges are candidates, not verified facts."], knownAt: null, onChanged: noop }));
  it("shows the not-covered placeholder for an empty graph, never a zero-filled chart", () => {
    const out = render([]);
    expect(out).toContain("Not covered");
    expect(out).toContain("No relationships are recorded");
  });
  it("renders candidates dashed and labelled, with consolidation grouped apart from ownership", () => {
    const out = render([
      edge({ id: "1" }),
      edge({ id: "2", edgeType: "parent", objectName: "Parent Holding", status: "confirmed", tier: "primary" }),
      edge({ id: "3", edgeType: "owner", objectName: "Big Fund", status: "disputed" }),
      edge({ id: "4", edgeType: "customer", objectName: "Old Co", status: "rejected", validTo: "2024-06-01" }),
    ]);
    expect(out).toContain("Consolidation and control");
    expect(out).toContain("Ownership and investment");
    expect(out).toContain("Suggested");
    expect(out).toContain("Disputed");
    expect(out).toContain("1 of 3 live relationships are confirmed facts");
    expect(out).toContain("1 of 3 edges confirmed");
    expect(out).toContain("Typed company-graph edges");
  });
  it("renders undated edges without a bar", () => {
    const out = render([edge({ validFrom: null, validTo: null })]);
    expect(out).toContain("no validity dates");
  });
});

describe("PatentMatrix", () => {
  const render = (patents: PatentFamily[]) => html(createElement(PatentMatrix, { patents, trends: null, asOfDate: null, knownAt: null, limitations: [] }));
  it("states the signal-not-value caveat and the denominator", () => {
    const out = render([patent(), patent({ familyKey: "F2", attributed: false })]);
    expect(out).toContain("not a measure of patent value");
    expect(out).toContain("1 attributed families; 1 placed");
    expect(out).toContain("excluded");
    expect(out).toContain("H01L");
  });
  it("shows not covered with no attributed family", () => {
    expect(render([patent({ attributed: false })])).toContain("Not covered");
    expect(render([])).toContain("No patent family with a dated ownership path");
  });
});

describe("peers and leadership", () => {
  it("renders ranked peers, unranked ones without a rank and the empty state", () => {
    const out = html(
      createElement(PeerRanking, {
        rows: [
          { peer: "AAA", peerFamilies: 3, ownFamilies: 5, overlapShare: 0.6, jaccard: 0.4, sharedSubclasses: ["H01L"], interpretation: "" },
          { peer: "ZZZ", peerFamilies: 0, ownFamilies: 5, overlapShare: null, jaccard: null, sharedSubclasses: [], interpretation: "" },
        ],
        asOfDate: null,
      }),
    );
    expect(out).toContain("not ranked");
    expect(out).toContain("1 of 2 confirmed peers can be ranked");
    expect(html(createElement(PeerRanking, { rows: [], asOfDate: null }))).toContain("nothing to compare");
  });
  it("renders leadership with an empty and a populated list", () => {
    expect(html(createElement(LeadershipPanel, { net: net(), asOfDate: null, knownAt: null }))).toContain("No executive edge");
    const out = html(createElement(LeadershipPanel, { net: net({ edges: [edge({ edgeType: "executive", objectName: "Ann Lee", status: "confirmed" })] }), asOfDate: null, knownAt: null }));
    expect(out).toContain("Ann Lee");
  });
});

describe("deals", () => {
  const payload = (over: Partial<DealsPayload["deals"][number]> = {}): DealsPayload => ({
    ticker: "T",
    knownAsOf: null,
    limitations: [],
    deals: [
      {
        dealKey: "T:deal:acme", eventType: "deal", counterpartyKey: "Acme", state: "approved", confirmedState: "announced", confirmedTerms: { currency: "USD", total_value: 1000 },
        timeline: [
          { eventId: 1, state: "announced", status: "confirmed", date: "2026-01-05", knownAt: null, evidenceId: 3, source: "8-K", sourceUrl: "https://example.com/8k", tier: "primary" },
          { eventId: 2, state: "approved", status: "suggested", date: null, knownAt: null, evidenceId: null, source: "news", sourceUrl: null, tier: "vendor" },
        ],
        ignored: [], proForma: null, proFormaBasis: null, proFormaUnavailable: "no confirmed terms", ...over,
      },
    ],
  });
  it("flags a provisional state and lists the five stages", () => {
    const out = html(createElement(DealsTimeline, { ticker: "T", data: payload(), asOfDate: null, onChanged: noop }));
    for (const s of ["Announcement", "Amendment", "Approval", "Closing", "Termination"]) expect(out).toContain(s);
    expect(out).toContain("suggested, not confirmed");
    expect(out).toContain("1,000 USD");
  });
  it("renders no deals without inventing one", () => {
    const out = html(createElement(DealsTimeline, { ticker: "T", data: { ticker: "T", knownAsOf: null, deals: [], limitations: [] }, asOfDate: null, onChanged: noop }));
    expect(out).toContain("No deal or financing event is stored");
    expect(out).toContain("Not covered");
  });
});

describe("concentration", () => {
  const data = (periods: ConcentrationPayload["periods"]): ConcentrationPayload => ({ ticker: "T", periods, limitations: [] });
  it("shows a floor when a share is missing and the not-covered state when empty", () => {
    const out = html(
      createElement(ConcentrationPanel, {
        asOfDate: null,
        data: data([{ fiscalPeriod: "FY2025", relationship: "customer", shareBasis: "issuer_revenue", namedCount: 2, anonymousCount: 1, namedShareSum: 0.2, anonymousShareSum: 0.1, reportedShareSum: 0.3, missingShareCount: 1, namedMissingShareCount: null, anonymousMissingShareCount: null, namedReportedShareSum: null, anonymousReportedShareSum: null, totalReportedShareSum: null, namedAtLeast: null, anonymousAtLeast: null, totalAtLeast: null, largestShare: 0.15, shareExceedsTotal: false, openContradictions: 0 }]),
      }),
    );
    expect(out).toContain("at least");
    expect(out).toContain("not imputed");
    expect(html(createElement(ConcentrationPanel, { data: data([]), asOfDate: null }))).toContain("Not covered");
  });
  it("lists knock-on links only for this holding", () => {
    const out = html(
      createElement(KnockOnPanel, {
        ticker: "A",
        asOfDate: null,
        data: { knownAsOf: null, limitations: [], links: [{ ticker: "A", counterpartyTicker: "B", relationship: "customer", direction: "issuer_sells_to", reportedShare: 0.1, shareBasis: "issuer_revenue", fiscalPeriod: "FY2025", source: "disclosure:1" }, { ticker: "C", counterpartyTicker: "D", relationship: "supplier", direction: "issuer_buys_from", reportedShare: null, shareBasis: null, fiscalPeriod: null, source: "edge:2" }] },
      }),
    );
    expect(out).toContain("1 confirmed link");
    expect(out).not.toContain(">D<");
  });
});

describe("changes, thesis and research panels", () => {
  it("asks for a date instead of listing changes without one", () => {
    const out = html(createElement(ChangesList, { net: net({ edges: [edge()] }), since: "", onSince: noop, sinceOrigin: null, asOfDate: null }));
    expect(out).toContain("Choose a date");
    expect(out).toContain("No monthly review is on record");
  });
  it("lists rows recorded after the review date", () => {
    const out = html(createElement(ChangesList, { net: net({ edges: [edge()] }), since: "2026-01-15", onSince: noop, sinceOrigin: "Defaults to the latest monthly review.", asOfDate: null }));
    expect(out).toContain("1 row was recorded after");
    expect(out).toContain("Acme Corp");
  });
  it("renders KPI links, observations and reconciliations in empty states", () => {
    expect(html(createElement(KpiLinksPanel, { links: [], asOfDate: null }))).toContain("No company event is linked");
    const out = html(createElement(ResearchPanel, { observations: [], reconciliations: [], asOfDate: null }));
    expect(out).toContain("No headcount or funding observation");
    expect(out).toContain("None open");
  });
});

describe("ResourceBoundary", () => {
  const render = (state: ResourceState<Parsed<number>>) => html(ResourceBoundary<number>({ state, title: "Panel", children: (v, stale) => createElement("p", null, `value ${v}${stale ? " stale" : ""}`) }));
  it("covers loading, error, error with last value, rejected shape and success", () => {
    expect(render({ kind: "loading" })).toContain("Loading");
    expect(render({ kind: "error", title: "Backend unreachable", detail: "x", previous: null })).toContain("Temporarily unavailable");
    const keep = render({ kind: "error", title: "HTTP 500", detail: "", previous: { ok: true, value: 7 } });
    expect(keep).toContain("value 7 stale");
    expect(keep).toContain("refresh failed");
    expect(render({ kind: "ok", data: { ok: false, reason: "edges: expected a list" } })).toContain("does not recognise");
    expect(render({ kind: "ok", data: { ok: true, value: 3 } })).toContain("value 3");
  });
});
