import { historyAt, type CheckOut, type DerivedOut, type FiledFact, type MaturityBucket, type SegmentBridge, type StatementsOut } from "./financials";

/*
 * Sample statements for the invented "Acme Corp" (the same company and dates as fixtures.ts):
 * the original 10-K (known 20 Feb 2026) reports revenue 1,000m and the 10-K/A (known 2 May 2026)
 * restates it to 950m. Built through `historyAt`, so the sample cannot disagree with the rules.
 */

const A1 = "0000001234-26-000003"; // the original 10-K
const A2 = "0000001234-26-000009"; // the 10-K/A
const K1 = "2026-02-20T23:59:59Z";
const K2 = "2026-05-02T23:59:59Z";
const FY24 = "2024-12-31";
const FY25 = "2025-12-31";

const f = (metric: string, end: string, value: string | null, known = K1, accession = A1): FiledFact => ({
  metric,
  period_end: end,
  value,
  known_at: known,
  accession,
});

export const SAMPLE_FACTS: FiledFact[] = [
  f("revenue", FY24, "900000000", "2025-02-18T23:59:59Z", "0000001234-25-000004"),
  f("revenue", FY25, "1000000000"),
  f("revenue", FY25, "950000000", K2, A2),
  f("cost_of_revenue", FY24, "560000000", "2025-02-18T23:59:59Z", "0000001234-25-000004"),
  f("cost_of_revenue", FY25, "600000000"),
  f("gross_profit", FY24, "340000000", "2025-02-18T23:59:59Z", "0000001234-25-000004"),
  f("gross_profit", FY25, "400000000"),
  f("gross_profit", FY25, "350000000", K2, A2),
  f("operating_cash_flow", FY24, "410000000", "2025-02-18T23:59:59Z", "0000001234-25-000004"),
  f("operating_cash_flow", FY25, "500000000"),
  f("capex", FY24, "95000000", "2025-02-18T23:59:59Z", "0000001234-25-000004"),
  f("capex", FY25, "120000000"),
  // Debt repaid is shown as a dash in the filing: no value, which is not zero.
  f("net_income", FY25, null),
];

const within = (a: number, b: number, tol = 0.005) => Math.abs(a - b) <= Math.max(Math.abs(a), Math.abs(b)) * tol;

/** The statements response the backend would return for Acme at a cutoff. */
export function sampleStatements(cutoffMs: number): StatementsOut {
  const history = historyAt(SAMPLE_FACTS, cutoffMs);
  const get = (m: string, end: string) => {
    const p = history.find((h) => h.metric === m && h.period_end === end);
    return p?.value == null ? null : Number(p.value);
  };
  const derived: DerivedOut[] = [];
  const checks: Record<string, CheckOut[]> = {};
  const usable: Record<string, boolean> = {};
  for (const end of [...new Set(history.map((h) => h.period_end))].sort()) {
    const [rev, cost, gp] = [get("revenue", end), get("cost_of_revenue", end), get("gross_profit", end)];
    const c: CheckOut[] = [];
    if (rev == null || cost == null || gp == null) {
      c.push({ name: "gross_profit", status: "needs_review", detail: "revenue, cost of revenue and gross profit must all be reported", expected: null, actual: null, tolerance: "0.005" });
    } else {
      const ok = within(rev - cost, gp);
      c.push({ name: "gross_profit", status: ok ? "pass" : "fail", detail: `revenue minus cost of revenue: expected ${rev - cost}, found ${gp}`, expected: String(rev - cost), actual: String(gp), tolerance: "0.005" });
    }
    const [op, capex] = [get("operating_cash_flow", end), get("capex", end)];
    if (op != null && capex != null && capex >= 0) {
      derived.push({
        metric: "free_cash_flow",
        period_end: end,
        value: String(op - capex),
        formula: "operating_cash_flow - capex",
        formula_version: "formulas-1",
        inputs: [history.find((h) => h.metric === "operating_cash_flow" && h.period_end === end)!.accession, history.find((h) => h.metric === "capex" && h.period_end === end)!.accession],
      });
    }
    checks[end] = c;
    usable[end] = c.every((x) => x.status === "pass");
  }
  return {
    issuer_id: 1,
    as_of: new Date(cutoffMs).toISOString(),
    taxonomy_version: "metrics-1",
    formula_version: "formulas-1",
    history,
    derived,
    checks,
    usable_for_valuation: usable,
    valuation_gate: Object.fromEntries(Object.entries(usable).map(([end, ok]) => [end, { usable_for_valuation: ok, reasons: ok ? [] : ["A statement check failed or needs review."] }])),
    formulas: [],
    unmapped_concepts: 0,
  };
}

/** Segment bridge for FY2025 as known at a cutoff; the restated total applies only after the 10-K/A. */
export function sampleBridge(cutoffMs: number): SegmentBridge {
  const restated = cutoffMs >= Date.parse(K2);
  const total = restated ? 950_000_000 : 1_000_000_000;
  const geo = restated ? [560, 340, 50] : [590, 350, 60];
  const prod = restated ? [700, 250, -10] : [740, 270, -10];
  const m = (member: string, v: number, eliminations = false) => ({ member, value: String(v * 1_000_000), eliminations });
  const sum = (xs: number[]) => xs.reduce((a, b) => a + b, 0) * 1_000_000;
  const prodMembers = prod.slice(0, 2); // eliminations are kept apart from the members
  return {
    period_end: FY25,
    total: String(total),
    axes: [
      {
        axis: "StatementGeographicalAxis",
        members: [m("US", geo[0]), m("EMEA", geo[1]), m("Other", geo[2])],
        members_sum: String(sum(geo)),
        eliminations: "0",
        unallocated: String(total - sum(geo)),
        reconciles: within(total, sum(geo)),
      },
      {
        axis: "ProductOrServiceAxis",
        members: [m("Widgets", prod[0]), m("Services", prod[1]), m("Eliminations", prod[2], true)],
        members_sum: String(sum(prodMembers)),
        eliminations: String(prod[2] * 1_000_000),
        unallocated: String(total - sum(prodMembers) - prod[2] * 1_000_000),
        reconciles: within(total, sum(prodMembers) + prod[2] * 1_000_000),
      },
    ],
    notes: [
      ...(within(total, sum(prodMembers) + prod[2] * 1_000_000) ? [] : ["ProductOrServiceAxis: members and eliminations are short of the reported total"]),
      "axes describe the same total from different angles and are not added together",
    ],
    metrics: [],
    cross_axis: [],
  };
}

/** Debt maturity buckets by currency, from a note table. Live data has none yet: notes are text blocks. */
export const SAMPLE_MATURITIES: { currency: string; total: number; buckets: MaturityBucket[] }[] = [
  {
    currency: "USD",
    total: 1_000_000_000,
    buckets: [
      { label: "2027", value: 100_000_000 },
      { label: "2028", value: 200_000_000 },
      { label: "2029", value: 150_000_000 },
      { label: "Later", value: 550_000_000 },
    ],
  },
  {
    currency: "EUR",
    total: 100_000_000,
    buckets: [
      { label: "2027", value: 40_000_000 },
      { label: "2029", value: 60_000_000 },
    ],
  },
];
