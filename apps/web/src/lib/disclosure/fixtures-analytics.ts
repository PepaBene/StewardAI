import type { CoverageCell, CoverageReport } from "@/lib/disclosure/coverage";
import type { LookThrough, SampleHolding } from "@/lib/disclosure/screen";

/** Invented portfolio used by the sample mode of the coverage and screen pages. Nothing here is real. */
export const SAMPLE_AS_OF = "2026-10-07T12:00:00Z";

const cell = (dataset: string, label: string, jurisdiction: string, o: Partial<CoverageCell> & { tickers: number; covered: number; denominator_value: string; covered_value: string }): CoverageCell => ({
  dataset,
  label,
  jurisdiction,
  uncovered: o.tickers - o.covered,
  stale: 0,
  uncovered_value: String(Number(o.denominator_value) - Number(o.covered_value)),
  covered_share: Number(o.denominator_value) > 0 ? String(Number(o.covered_value) / Number(o.denominator_value)) : null,
  first_known_at: null,
  last_known_at: null,
  states: {},
  blind_spots: [],
  unit: "records",
  source: "Invented sample source",
  method: "sample",
  ...o,
});

export const SAMPLE_COVERAGE: CoverageReport = {
  as_of: SAMPLE_AS_OF,
  snapshot_date: "2026-10-06",
  holdings: 5,
  watchlist_only: 0,
  denominator_value: "10000",
  cells: [
    cell("filings", "Filings", "US", { tickers: 3, covered: 3, denominator_value: "7000", covered_value: "7000", first_known_at: "2024-02-20T00:00:00Z", last_known_at: "2026-09-30T00:00:00Z", states: { available: 3 } }),
    cell("filings", "Filings", "GB", { tickers: 2, covered: 0, denominator_value: "3000", covered_value: "0", states: { none_known: 1, unresolved_identity: 1 }, blind_spots: ["2 held with no record: SAMPLEUK, SAMPLEPLC", "Only issuers linked to a CIK are discovered; non-US issuers need a registered adapter"] }),
    cell("insiders", "Insider activity", "US", { tickers: 3, covered: 2, stale: 1, denominator_value: "7000", covered_value: "5500", first_known_at: "2025-03-01T00:00:00Z", last_known_at: "2026-09-12T00:00:00Z", states: { available: 1, stale: 1, none_known: 1 }, blind_spots: ["1 held with no record: SAMPLEC"] }),
    cell("insiders", "Insider activity", "GB", { tickers: 2, covered: 1, denominator_value: "3000", covered_value: "1200", first_known_at: "2026-04-02T00:00:00Z", last_known_at: "2026-08-30T00:00:00Z", states: { available: 1, none_known: 1 } }),
    cell("estimates", "Published estimates", "US", { tickers: 3, covered: 1, denominator_value: "7000", covered_value: "3000", first_known_at: "2026-05-01T00:00:00Z", last_known_at: "2026-07-28T00:00:00Z", states: { available: 1, paid_only: 1, none_known: 1 }, blind_spots: ["Named analyst estimates have no free route"] }),
    cell("estimates", "Published estimates", "GB", { tickers: 2, covered: 0, denominator_value: "3000", covered_value: "0", states: { unresolved_identity: 2 } }),
  ],
  by_period: [
    { dataset: "financials", period: "2024", issuers_covered: 2, issuers_total: 3, covered_value: "5000", denominator_value: "10000" },
    { dataset: "financials", period: "2025", issuers_covered: 3, issuers_total: 3, covered_value: "7000", denominator_value: "10000" },
  ],
  failures: [{ kind: "source", dataset: "sedar_plus", jurisdiction: "CA", state: "authentication_failed", count: 1, last_checked_at: "2026-10-05T08:00:00Z", detail: "Session expired" }],
  quality: [
    { dataset: "relationship_claim", labelled: 0, errors: 0, error_rate: null, needed: 100, bar: "0.95", state: "not_sampled" },
    { dataset: "insider_class", labelled: 52, errors: 1, error_rate: "0.0192", needed: 50, bar: "0.98", state: "meets_bar" },
    { dataset: "ownership_match", labelled: 20, errors: 2, error_rate: "0.1000", needed: 50, bar: "0.98", state: "sample_too_small" },
  ],
  cost_ledger: { total_usd: "0.00", basis: "Every registered source is constrained to cost = 'free'.", registered_sources: 6, paid_sources: 0, sources: [{ name: "SEC EDGAR", reference: "https://www.sec.gov/edgar", cost_usd: "0.00" }] },
  release_order: [
    { order: 1, stage: "Verified SEC and point-in-time foundation", datasets: ["filings", "financials"], gates: { filings: null, financials: "not_sampled" }, state: "gates_open" },
    { order: 2, stage: "Ownership and insiders", datasets: ["ownership", "insiders"], gates: { ownership: "sample_too_small", insiders: "meets_bar" }, state: "gates_open" },
    { order: 5, stage: "Additional jurisdictions", datasets: [], gates: {}, state: "not_started" },
  ],
  first_known_at: "2024-02-20T00:00:00Z",
  last_known_at: "2026-09-30T00:00:00Z",
  method_version: "coverage.v1",
  notes: ["Covered means a record known by the date is stored; uncovered is not proof none exists.", "A rate or share with no denominator is null, never zero."],
};

const f = (value: number | boolean, period: string | null, source: string) => ({ value, period, source });

export const SAMPLE_UNIVERSE: SampleHolding[] = [
  { ticker: "SAMPLEA", value: 4000, fields: { cash_conversion: f(1.3, "2025-12-31", "verified observations"), customer_concentration: f(0.32, "FY2025", "confirmed relationship"), discretionary_buys: f(2, "last 90 days", "Form 4 filings") } },
  { ticker: "SAMPLEB", value: 3000, fields: { cash_conversion: f(0.7, "2025-12-31", "verified observations"), discretionary_buys: f(0, "last 90 days", "Form 4 filings"), deal_exposure: f(true, null, "public deal documents") } },
  { ticker: "SAMPLEC", value: 2000, fields: { stake_change_pct: f(-3.1, "2026-08-14", "Schedule 13D/13G") } },
  { ticker: "SAMPLEUK", value: 1000, fields: {} },
];

export const SAMPLE_LOOK: LookThrough = {
  as_of: SAMPLE_AS_OF,
  snapshot_date: "2026-10-06",
  denominator_value: "10000",
  holdings: 4,
  exposures: [{ ticker: "SAMPLEA", direct_weight: "0.4", held_directly: true, fund_weight: "0.03", total_weight: "0.43", funds: [{ fund_ticker: "SAMPLEETF", fund_weight: "0.3", fund_pct: "0.1", contribution: "0.03", report_period_end: "2026-06-30", lag_days: 99 }] }],
  relationships: [
    { ticker: "SAMPLEA", relationship: "customer", counterparty_key: null, counterparty_name: "Northwind Traders", anonymous: false, share: "0.32", share_basis: "total_revenue", fiscal_period: "FY2025", known_at: "2026-02-20T00:00:00Z", source_url: null, holding_weight: "0.43", weighted_share: "0.1376", weight_state: "reported", concentrated_customer: true },
    { ticker: "SAMPLEB", relationship: "supplier", counterparty_key: null, counterparty_name: null, anonymous: true, share: null, share_basis: null, fiscal_period: "FY2025", known_at: "2026-02-21T00:00:00Z", source_url: null, holding_weight: "0.3", weighted_share: null, weight_state: "unknown", concentrated_customer: false },
  ],
  deals: [{ ticker: "SAMPLEB", deal_id: 1, title: "SAMPLEB acquires Widgetco", deal_type: "acquisition", state: "agreed", direct_weight: "0.3", holding_weight: "0.3" }],
  funds: [{ fund_ticker: "SAMPLEETF", weight: "0.3", report_period_end: "2026-06-30", known_at: "2026-08-29T00:00:00Z", lag_days: 99, mapped_pct: "0.4", unmapped_pct: "0.55", unknown_lines: 2 }],
  totals: {
    denominator_weight: "1",
    relationship_covered_weight: "0.73",
    relationship_covered_holdings: 2,
    unknown_weight_holdings: ["SAMPLEB"],
    unknown_weight: "0.3",
    customer_known_weight: "0.43",
    concentrated_customer_weight: "0.43",
    concentrated_customer_holdings: ["SAMPLEA"],
    largest_customer_weighted: "0.1376",
    deal_exposed_weight: "0.3",
    deal_exposed_holdings: ["SAMPLEB"],
    fund_look_through_weight: "0.03",
    unmapped_fund_weight: "0.165",
  },
  as_of_dates: [
    { kind: "position_snapshot", dates: ["2026-10-06"] },
    { kind: "fund_report", dates: ["2026-06-30"] },
  ],
  mixed_as_of: true,
  max_fund_lag_days: 99,
  concentration_share: "0.10",
  method_version: "look_through.v1",
  notes: ["Every total is a share of the whole portfolio market value; the denominator is returned."],
};
