import { concentrationOf, shockScenario, type Claim, type DependencyView, type PortfolioHolding, type SEdge } from "@/lib/disclosure/dependencies";

/*
 * Invented companies so the screen can be shown without touching real filings. The sample has every
 * claim shape (named, anonymous, group, no share, a contract award), one claim that is only a
 * suggestion, one that was confirmed part-way through, and one that is a negated passage.
 */

export const SAMPLE_DEP_TICKER = "ACME";
export const SAMPLE_DEP_DAY = "2026-06-30";

type Seed = Partial<Claim> & Pick<Claim, "id" | "relationship" | "shape" | "passage" | "known_at">;
/** When a claim became confirmed, if after it was first known. Before this it was a suggestion. */
const CONFIRMED_ON: Record<number, string> = { 2: "2026-04-01T00:00:00Z" };

const claim = (s: Seed): Claim => ({
  status: "confirmed",
  counterparty: null,
  counterparty_key: null,
  counterparty_count: null,
  share: null,
  denominator: null,
  period_end: null,
  valid_from: null,
  valid_to: null,
  source_url: "https://www.sec.gov/Archives/edgar/data/acme/10-K-2025.htm",
  evidence_id: null,
  problems: [],
  screen: [],
  stale: false,
  entity: null,
  awards: [],
  ...s,
});

const SEEDS: Seed[] = [
  { id: 1, relationship: "customer", shape: "named", counterparty: "Borealis Systems", counterparty_key: "BORE", share: "0.22", denominator: "total_revenue", period_end: "2025-12-31", known_at: "2026-02-20T00:00:00Z", passage: "Borealis Systems accounted for 22% of our total revenue in fiscal 2025.", evidence_id: 9001 },
  { id: 2, relationship: "customer", shape: "named", counterparty: "Cinder Retail", counterparty_key: "CNDR", share: "0.14", denominator: "total_revenue", period_end: "2025-12-31", known_at: "2026-02-20T00:00:00Z", passage: "Sales to Cinder Retail represented 14% of total revenue.", evidence_id: 9002 },
  { id: 3, relationship: "customer", shape: "anonymous", counterparty: null, share: "0.11", denominator: "total_revenue", period_end: "2025-12-31", known_at: "2026-02-20T00:00:00Z", passage: "Customer A accounted for 11% of total revenue.", evidence_id: 9003 },
  { id: 4, relationship: "customer", shape: "group", counterparty: null, counterparty_count: 3, share: "0.38", denominator: "total_revenue", period_end: "2025-12-31", known_at: "2026-02-20T00:00:00Z", passage: "Our three largest customers together accounted for 38% of total revenue.", evidence_id: 9004 },
  { id: 5, relationship: "customer", shape: "named", counterparty: "Dune Mobility", counterparty_key: "DUNE", entity: { status: "candidate", basis: "name", needs_review: true, candidates: [412], note: "A name match only: confirm it before it is linked." }, share: "0.30", denominator: "total_revenue", period_end: "2025-12-31", status: "suggested", known_at: "2026-05-10T00:00:00Z", passage: "If we were to lose Dune Mobility, which we expect could account for 30% of revenue, our results would suffer.", screen: ["hypothetical"], evidence_id: 9005 },
  { id: 6, relationship: "customer", shape: "named", counterparty: "Eland Cloud", counterparty_key: "ELND", share: "0.09", denominator: "segment_revenue", period_end: "2025-12-31", known_at: "2026-02-20T00:00:00Z", passage: "Eland Cloud accounted for 9% of Services segment revenue.", evidence_id: 9006 },
  { id: 7, relationship: "supplier", shape: "named", counterparty: "Corvid Components", counterparty_key: "CRVD", share: "0.41", denominator: "purchases", period_end: "2025-12-31", known_at: "2026-02-20T00:00:00Z", passage: "Corvid Components supplied 41% of our component purchases.", evidence_id: 9007 },
  { id: 8, relationship: "supplier", shape: "named", counterparty: "Fathom Foundry", counterparty_key: "FTHM", known_at: "2026-02-20T00:00:00Z", passage: "We depend on Fathom Foundry as the sole source of one assembly.", evidence_id: 9008, stale: true },
  { id: 9, relationship: "procurement_award", shape: "named", awards: [{ notice_number: "2026/S 051-123456", lot_id: "LOT-1", notice_type: "can-standard", cpv_codes: ["34144900"], value: "2400000", currency: "EUR", start_date: "2026-04-01", end_date: null, status: "awarded", published: "2026-03-12" }], counterparty: "Ministry of Transport (CZ)", known_at: "2026-03-12T00:00:00Z", passage: "Contract award notice 2026/S 051: supply of fleet sensors to ACME Corp.", source_url: "https://ted.europa.eu/notice/2026-051", evidence_id: null },
  { id: 10, relationship: "customer", shape: "named", counterparty: "Gantry Freight", counterparty_key: "GNTR", share: "0.05", denominator: "total_revenue", period_end: "2025-12-31", status: "suggested", known_at: "2026-06-01T00:00:00Z", passage: "No customer other than Gantry Freight exceeded 5% of revenue.", screen: ["negated"], evidence_id: 9010 },
];

export function sampleClaims(cutoffMs: number): Claim[] {
  return SEEDS.filter((s) => Date.parse(s.known_at) <= cutoffMs).map((s) => {
    const confirmedAt = CONFIRMED_ON[s.id];
    // a claim confirmed after the replayed date was still only a suggestion then
    const status = confirmedAt && Date.parse(confirmedAt) > cutoffMs ? "suggested" : s.status;
    return claim({ ...s, status: status ?? "confirmed" });
  });
}

/** What other companies say about their own customers: the second hop of the scenario. */
const EDGES: SEdge[] = [
  { id: 1, supplier: "ACME", customer: "BORE", share: 0.22, status: "confirmed", knownMs: Date.parse("2026-02-20T00:00:00Z") },
  { id: 2, supplier: "CNDR_SUP", customer: "ACME", share: 0.4, status: "confirmed", knownMs: Date.parse("2026-03-01T00:00:00Z") },
  { id: 3, supplier: "ACME", customer: "NORD", share: null, status: "confirmed", knownMs: Date.parse("2026-03-01T00:00:00Z") },
  { id: 4, supplier: "MICRO", customer: "CNDR_SUP", share: 0.5, status: "confirmed", knownMs: Date.parse("2026-03-10T00:00:00Z") },
  { id: 5, supplier: "HAULR", customer: "ACME", share: 0.35, status: "suggested", knownMs: Date.parse("2026-05-01T00:00:00Z") },
];

export const SAMPLE_NAMES: Record<string, string> = { ACME: "Acme Corp", BORE: "Borealis Systems", CNDR_SUP: "Cobalt Parts", MICRO: "Micro Alloys", NORD: "Nordlicht GmbH", HAULR: "Haulr Logistics" };

export function sampleDependencies(cutoffMs: number, shock = 0.2): DependencyView {
  const claims = sampleClaims(cutoffMs);
  const s = shockScenario(EDGES, "BORE", shock, cutoffMs);
  return {
    ticker: SAMPLE_DEP_TICKER,
    as_of: new Date(cutoffMs).toISOString(),
    claims,
    concentration: concentrationOf(claims),
    scenarios: [
      {
        origin: "BORE",
        shock: String(shock),
        impacts: s.impacts.map((i) => ({ entity: i.entity, hop: i.hop, lower: String(i.lower), upper: String(i.upper), coverage: String(i.coverage), via: i.via, source_age_days: 0, uses_owner_assigned: false })),
        non_quantified: s.nonQuantified.map((e) => ({ supplier: e.supplier, customer: e.customer, edge_id: e.id, note: "A confirmed relationship with no reported share: not quantified" })),
        notes: ["Only confirmed, dated edges with a reported share carry numbers.", "The upper bound treats every undisclosed customer as hit as hard as the origin."],
      },
    ],
    conflicts: claims.some((c) => c.id === 1)
      ? [{ kind: "share_mismatch", claim_ids: [1], note: "Borealis Systems says it bought 18% of its supply from Acme; Acme reports 22% of its revenue. Both are kept." }]
      : [],
    owner_weights: [{ id: 1, counterparty: "Nordlicht GmbH", counterparty_key: "NORD", share: "0.06", denominator: "total_revenue", as_of: "2026-05-02", note: "Estimated from a call with management; not in any filing.", assigned_by: "owner" }],
    inbound_claims: 1,
    notes: claims.length === 0 ? ["No relationship claims are known for this holding by that date."] : [],
  };
}

/** A small invented portfolio: weights add to less than 100% so the remainder is visible. */
export function samplePortfolio(cutoffMs: number): PortfolioHolding[] {
  const acme = sampleClaims(cutoffMs);
  const other = (id: number, name: string | null, share: string, shape: Claim["shape"] = "named"): Claim =>
    claim({ id, relationship: "customer", shape, counterparty: name, share, denominator: "total_revenue", period_end: "2025-12-31", known_at: "2026-02-20T00:00:00Z", passage: "Reported share of revenue." });
  return [
    { ticker: "ACME", weight: 0.3, claims: acme },
    { ticker: "ORBT", weight: 0.2, claims: [other(101, "Borealis Systems", "0.35"), other(102, null, "0.2", "anonymous")] },
    { ticker: "PYLN", weight: 0.15, claims: [other(201, "Cinder Retail", "0.18")] },
  ];
}
