import { lanesOf, revisionPairs, SERIES, SURPRISE_FORMULA, surpriseFor, visibleAt, type Actual, type CoverageGap, type EstimateLine, type EstimatesView, type SegmentBridge, type SegmentLine } from "@/lib/disclosure/estimates";

/*
 * An invented company ("Acme Instruments") so the screen can be shown without touching real files. The
 * sample holds, on purpose: a panel replacement (a break), a changed adjusted definition (a break), a
 * corrected IR file published after the earnings release (it must not score the earlier surprise), a
 * company guidance bracket, an event-calendar estimate, an owner scenario, no named analyst estimates
 * (a coverage gap), a segment with no published estimate and an elimination line.
 */

export const SAMPLE_EST_TICKER = "ACME";
export const SAMPLE_EST_DAY = "2026-08-20";
const RELEASE = "2026-07-28T12:00:00Z";
const IR_URL = "https://ir.acme.example/consensus/2026q3";
const M1 = "consensus compiled by investor relations from analysts";
const M2 = "vendor consensus";

type Seed = Partial<EstimateLine> & Pick<EstimateLine, "id" | "snapshot_id" | "known_at">;
const line = (s: Seed): EstimateLine => ({
  series: "published_analyst_consensus", metric: "Revenue", segment: null, segment_taxonomy: null, non_additive: false, fiscal_period: "FY2026Q3", fiscal_calendar: "fiscal year ends 30 September",
  period_start: "2026-04-01", period_end: "2026-06-30", currency: "USD", unit: "amount", scale: 6, basis: "GAAP", basis_definition: null, mean: null, median: null, high: null, low: null,
  analyst_count: 14, panel_analyst_count: 14, panel_description: "Consensus compiled by Acme investor relations from analysts", panel_method: M1, source: "Acme investor relations", broker: null, analyst: null,
  published_at: s.known_at, supersedes_snapshot_id: null, corrected: false, document_url: IR_URL, evidence_id: null, original_string: null, problems: [], ...s,
});

const q3 = (id: number, snap: number, known: string, mean: string, median: string, high: string, low: string, extra: Partial<EstimateLine> = {}) =>
  line({ id, snapshot_id: snap, known_at: known, mean, median, high, low, original_string: `Revenue | Q3 FY2026 | ${mean}`, ...extra });

const LINES: EstimateLine[] = [
  q3(1, 1, "2026-04-15T09:00:00Z", "1230.0", "1229.0", "1250.0", "1210.0"),
  q3(2, 2, "2026-05-10T09:00:00Z", "1236.0", "1235.0", "1255.0", "1220.0", { panel_method: M2, panel_description: "Vendor consensus, contributors vary", analyst_count: 11, panel_analyst_count: 11, source: "Vendor feed on Acme IR page" }),
  q3(3, 3, "2026-06-01T09:00:00Z", "1241.0", "1240.0", "1260.0", "1225.0", { panel_method: M2, analyst_count: 11, panel_analyst_count: 11 }),
  q3(4, 4, "2026-07-15T09:00:00Z", "1250.5", "1248.0", "1270.0", "1230.0", { panel_method: M2, analyst_count: 14, panel_analyst_count: 14 }),
  line({ id: 6, snapshot_id: 1, known_at: "2026-04-15T09:00:00Z", metric: "Revenue", fiscal_period: "FY2026", period_start: "2025-10-01", period_end: "2026-09-30", mean: "4950.0", median: "4948.0", high: "5010.0", low: "4890.0" }),
  line({ id: 7, snapshot_id: 4, known_at: "2026-07-15T09:00:00Z", fiscal_period: "FY2026", period_start: "2025-10-01", period_end: "2026-09-30", mean: "4980.0", median: "4975.0", high: "5040.0", low: "4910.0", panel_method: M2 }),
  line({ id: 8, snapshot_id: 3, known_at: "2026-06-01T09:00:00Z", metric: "Adjusted EPS", unit: "per_share", scale: 0, basis: "adjusted", basis_definition: "excludes stock compensation", mean: "1.40", median: "1.40", high: "1.46", low: "1.33", panel_method: M2, analyst_count: 11 }),
  line({ id: 9, snapshot_id: 4, known_at: "2026-07-15T09:00:00Z", metric: "Adjusted EPS", unit: "per_share", scale: 0, basis: "adjusted", basis_definition: "excludes stock compensation and amortization of acquired intangibles", mean: "1.42", median: "1.41", high: "1.50", low: "1.35", panel_method: M2, analyst_count: null }),
  line({ id: 10, snapshot_id: 6, known_at: "2026-04-30T09:00:00Z", series: "company_guidance", fiscal_period: "FY2026", period_start: "2025-10-01", period_end: "2026-09-30", low: "4900.0", high: "5050.0", analyst_count: null, panel_analyst_count: null, panel_description: null, panel_method: null, source: "Acme investor relations (management guidance)" }),
  line({ id: 11, snapshot_id: 7, known_at: "2026-07-20T09:00:00Z", series: "event_calendar_estimate", mean: "1255.0", analyst_count: null, panel_analyst_count: null, panel_description: "Single vendor estimate for the earnings event", panel_method: null, source: "Vendor event calendar" }),
  line({ id: 12, snapshot_id: 8, known_at: "2026-06-15T09:00:00Z", series: "owner_scenario", mean: "1280.0", analyst_count: null, panel_analyst_count: null, panel_description: null, panel_method: null, source: "Your scenario: base case", document_url: null }),
  ...[
    { id: 13, segment: "Industrial", mean: "700.0", median: "699.0", high: "715.0", low: "688.0" },
    { id: 14, segment: "Healthcare", mean: "560.0", median: "561.0", high: "572.0", low: "549.0" },
    { id: 15, segment: "Eliminations", mean: "-9.5", median: null, high: null, low: null, non_additive: true },
  ].map((s) => line({ snapshot_id: 4, known_at: "2026-07-15T09:00:00Z", segment_taxonomy: "fiscal 2026 structure", panel_method: M2, analyst_count: s.id === 15 ? null : 9, ...s })),
];

// The corrected IR file is a complete new file (ids +100) that replaces the 15 July one, 15 days
// after the earnings release. It changes the Q3 revenue mean to 1,252.0.
const CORRECTED: EstimateLine[] = LINES.filter((l) => l.snapshot_id === 4).map((l) => ({
  ...l, id: l.id + 100, snapshot_id: 5, known_at: "2026-08-12T09:00:00Z", published_at: "2026-08-12T09:00:00Z", supersedes_snapshot_id: 4,
  ...(l.id === 4 ? { mean: "1252.0", median: "1249.0", low: "1231.0", original_string: "Revenue | Q3 FY2026 | 1,252.0 (corrected file)" } : {}),
}));
LINES.push(...CORRECTED);

const ACTUALS: Actual[] = [
  { observation_id: 701, value: 1262, scale: 6, unit: "amount", currency: "USD", basis: "GAAP", released_at: RELEASE },
];
const SEG_ACTUALS = [
  { name: "Industrial", value: 712, nonAdd: false },
  { name: "Healthcare", value: 553, nonAdd: false },
  { name: "Services", value: 52, nonAdd: false },
  { name: "Eliminations", value: -9, nonAdd: true },
];

const SOURCES = [
  { source_key: "acme.ir.consensus", publisher: "Acme investor relations", origin: "issuer_compiled", format: "html", access_state: "available", gap_reason: null, url: IR_URL, cadence: "about monthly, before each release", tested_on: "2026-08-01", selected_metrics_only: true, selected_brokers_only: false },
];

const segmentBridge = (cutoff: number, lines: EstimateLine[]): SegmentBridge => {
  const loaded = cutoff >= Date.parse(RELEASE);
  const est = visibleAt(lines.filter((l) => l.segment), Date.parse(RELEASE), true);
  const rows: SegmentLine[] = [];
  if (loaded) {
    for (const a of SEG_ACTUALS) {
      const e = est.find((l) => l.segment === a.name);
      const ev = e?.mean != null ? Number(e.mean) * 1e6 : null;
      const av = a.value * 1e6;
      rows.push({
        segment: a.name, estimate_value_id: e?.id ?? null, actual_observation_id: 800 + rows.length, estimate: ev == null ? null : String(ev), actual: String(av), delta: ev == null ? null : String(av - ev),
        delta_pct: ev == null || ev === 0 ? null : String(((av - ev) / Math.abs(ev)) * 100), status: ev == null ? "estimate_missing" : "scored", non_additive: a.nonAdd || !!e?.non_additive,
        flags: a.nonAdd ? ["elimination or non-additive line"] : [], estimate_evidence_id: null, actual_evidence_id: 900 + rows.length,
      });
    }
  }
  const scored = rows.filter((r) => r.status === "scored" && !r.non_additive);
  const notes = loaded ? ["No segment estimate was published for: Services.", "Elimination and non-additive lines are shown but excluded from the totals.", `The totals add only the ${scored.length} additive segment(s) scored above.`] : ["Segment actuals are not loaded for this period."];
  return {
    metric: "Revenue", fiscal_period: "FY2026Q3", period_end: "2026-06-30", lines: rows,
    additive_estimate_total: scored.length ? String(scored.reduce((s, r) => s + Number(r.estimate), 0)) : null,
    additive_actual_total: scored.length ? String(scored.reduce((s, r) => s + Number(r.actual), 0)) : null,
    unmapped_estimates: loaded ? [] : est.filter((l) => !l.non_additive).map((l) => l.segment ?? ""), notes, inputs: scored.map((r) => `estimate:${r.estimate_value_id}`),
  };
};

export function sampleEstimates(cutoffMs: number): EstimatesView {
  const earlier = LINES.filter((l) => Date.parse(l.known_at) <= cutoffMs);
  const replaced = new Set(earlier.map((l) => l.supersedes_snapshot_id));
  const known = earlier.map((l) => ({ ...l, corrected: replaced.has(l.snapshot_id) }));
  const live = visibleAt(known, cutoffMs);
  const lanes = lanesOf(live);
  const consensus = known.filter((l) => l.series === "published_analyst_consensus");
  const loaded = cutoffMs >= Date.parse(RELEASE);
  const events = consensus.some((l) => l.metric === "Revenue" && l.period_end === "2026-06-30" && !l.segment)
    ? [surpriseFor(loaded ? ACTUALS[0] : null, consensus, "Revenue", "2026-06-30")]
    : [];
  const coverage: CoverageGap[] = [
    { kind: "named_estimates", state: "not_covered", detail: "No accessible source returned a named analyst and broker with metric, period and publication time. Broker-level values are never derived from a consensus mean or range." },
  ];
  for (const s of SERIES) if (s !== "named_individual_estimate" && lanes[s].length === 0) coverage.push({ kind: s, state: "not_covered", detail: "Nothing known in this lane at this date." });
  return {
    ticker: SAMPLE_EST_TICKER, as_of: new Date(cutoffMs).toISOString(), issuer: { id: 0, name: "Acme Instruments (invented)" },
    lanes: lanes as unknown as EstimatesView["lanes"], pairs: revisionPairs(live), events,
    segment_bridges: lanes.published_analyst_consensus.some((l) => l.segment) ? [segmentBridge(cutoffMs, consensus)] : [], coverage, sources: SOURCES,
    revision_gate: { reviewed: 12, needed: 20, correct: 11, rate: "0.9166666667", bar: "0.90", met: false, reason: "12 of 20 reviewed pairs" },
    formula: { surprise: SURPRISE_FORMULA, version: "surprise.v1" }, notes: [],
  };
}
