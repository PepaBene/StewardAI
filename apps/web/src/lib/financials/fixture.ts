import type {
  CashWalk,
  DebtSchedule,
  EarningsEvent,
  EstimateSet,
  ForwardPoint,
  PeerSet,
  SegmentSet,
  StatementLine,
  StatementSet,
} from "./types";

/*
 * Illustrative fixtures for the Phase 4 views, for an invented company ("Fixture Industrials").
 * Figures are synthetic but internally consistent: the cash walk and the debt maturities
 * reconcile to the filed totals except where a gap is planted on purpose. FY2023 carries a
 * restatement (discontinued operations) so point-in-time behaviour is visible.
 */

const FY = [2021, 2022, 2023, 2024, 2025];
const END = (y: number) => `${y}-12-31`;
const FILED = (y: number) => `${y + 1}-02-20`;

type Series = readonly number[];

function line(
  id: string,
  label: string,
  statement: StatementLine["statement"],
  unit: string,
  values: Series,
  restated: Record<number, { value: number; filedAt: string }> = {},
): StatementLine {
  return {
    id,
    label,
    statement,
    unit,
    periods: FY.map((y, i) => {
      const r = restated[y];
      return {
        period: `FY${y}`,
        end: END(y),
        versions: [
          { version: 1, value: values[i], filedAt: FILED(y), accession: `0000000000-${String(y + 1).slice(2)}-00${i + 1}`, restates: false },
          ...(r ? [{ version: 2, value: r.value, filedAt: r.filedAt, accession: `0000000000-${r.filedAt.slice(2, 4)}-9${i + 1}`, restates: true }] : []),
        ],
      };
    }),
  };
}

// Millions of USD. FY2023 revenue and operating income were restated in the FY2024 10-K for a discontinued unit.
const LINES: StatementLine[] = [
  line("revenue", "Revenue", "income", "USD m", [18420, 21310, 23880, 25100, 26750], {
    2023: { value: 22640, filedAt: "2025-02-18" },
    2022: { value: 20180, filedAt: "2025-02-18" },
  }),
  line("gross_profit", "Gross profit", "income", "USD m", [6310, 7480, 8290, 9020, 9740]),
  line("operating_income", "Operating income", "income", "USD m", [2410, 2950, 3380, 3720, 4090], { 2023: { value: 3190, filedAt: "2025-02-18" } }),
  line("net_income", "Net income", "income", "USD m", [1620, 1990, 2310, 2540, 2810]),
  line("eps", "Diluted EPS", "income", "USD/share", [2.31, 2.86, 3.34, 3.71, 4.12]),
  line("cash", "Cash and equivalents", "balance", "USD m", [2480, 2210, 2760, 3140, 3480]),
  line("total_debt", "Total debt", "balance", "USD m", [7150, 7420, 7240, 6980, 6740]),
  line("equity", "Shareholders' equity", "balance", "USD m", [9120, 9760, 10530, 11480, 12610]),
  line("cfo", "Cash from operations", "cashflow", "USD m", [2290, 2540, 3010, 3330, 3620]),
  line("capex", "Capital expenditure", "cashflow", "USD m", [-880, -1010, -1130, -1240, -1310]),
  line("buybacks", "Share repurchases", "cashflow", "USD m", [-410, -560, -720, -840, -910]),
];

export const STATEMENTS_FIXTURE: StatementSet = {
  company: "Fixture Industrials Inc (illustrative)",
  ticker: "FIXI",
  basis: "US GAAP",
  currency: "USD",
  fiscalYearNote: "Fiscal year ends 31 December",
  scale: "millions",
  lines: LINES,
  source: "SEC EDGAR filings (as filed) · 10-K",
  sourceUrl: "https://www.sec.gov/edgar/search/",
  state: "verified",
  reason: null,
};

export const CASH_WALK_FIXTURE: CashWalk = {
  period: "FY2025",
  currency: "USD",
  netIncome: 2810,
  adjustments: [
    { label: "Depreciation and amortisation", value: 1180 },
    { label: "Share-based compensation", value: 240 },
    { label: "Working capital", value: -520 },
    { label: "Other non-cash", value: -90 },
  ],
  reportedCfo: 3620,
  capex: -1310,
  reportedFcf: 2310,
};

export const DEBT_FIXTURE: DebtSchedule = {
  asOf: END(2025),
  baseYear: 2026,
  reportingCurrency: "USD",
  fxToReporting: { EUR: 1.09 },
  reportedTotal: 6740,
  tranches: [
    { year: 2026, currency: "USD", amount: 450 },
    { year: 2027, currency: "USD", amount: 800 },
    { year: 2027, currency: "EUR", amount: 500 },
    { year: 2028, currency: "USD", amount: 600 },
    { year: 2029, currency: "EUR", amount: 700 },
    { year: 2030, currency: "USD", amount: 900 },
    { year: 2032, currency: "USD", amount: 1100 },
    { year: 2035, currency: "USD", amount: 900 },
  ],
  citation: "10-K FY2025, Note 11 (Debt), maturity table",
};

export const SEGMENTS_FIXTURE: SegmentSet = {
  period: "FY2025",
  currency: "USD",
  totalRevenue: 26750,
  citation: "10-K FY2025, Note 18 (Segment information)",
  segments: [
    { name: "Automation", revenue: 9800, priorRevenue: 8900, operatingProfit: 1960, profitDefinition: "Segment operating income (GAAP)" },
    { name: "Power systems", revenue: 7300, priorRevenue: 7150, operatingProfit: 1020, profitDefinition: "Segment operating income (GAAP)" },
    { name: "Mobility", revenue: 5700, priorRevenue: 5400, operatingProfit: 620, profitDefinition: "Segment operating income (GAAP)" },
    { name: "Services", revenue: 3950, priorRevenue: 3650, operatingProfit: 790, profitDefinition: "Segment operating income (GAAP)" },
  ],
};

/** The same company before a re-segmentation: one segment reports adjusted profit, so margins are not comparable. */
export const SEGMENTS_MISALIGNED_FIXTURE: SegmentSet = {
  ...SEGMENTS_FIXTURE,
  period: "FY2024 (as first reported)",
  segments: SEGMENTS_FIXTURE.segments.map((s, i) => ({
    ...s,
    profitDefinition: i === 2 ? "Adjusted segment EBITA (non-GAAP)" : s.profitDefinition,
    priorRevenue: s.name === "Services" ? null : s.priorRevenue,
  })),
};

const fp = (p: Partial<ForwardPoint> & Pick<ForwardPoint, "kind" | "period" | "vintage">): ForwardPoint => ({
  metric: "Diluted EPS",
  low: null,
  mean: null,
  median: null,
  high: null,
  analystCount: null,
  basis: "GAAP, diluted",
  currency: "USD",
  provider: "Company-published consensus file (IR)",
  sourceUrl: null,
  ...p,
});

const consensus = (period: string, vintage: string, low: number, mean: number, median: number, high: number, n: number) =>
  fp({ kind: "published_analyst_consensus", period, vintage, low, mean, median, high, analystCount: n });

export const ESTIMATES_FIXTURE: EstimateSet = {
  metric: "Diluted EPS",
  unit: "USD per share",
  actuals: [
    { period: "FY2021", value: 2.31 },
    { period: "FY2022", value: 2.86 },
    { period: "FY2023", value: 3.34 },
    { period: "FY2024", value: 3.71 },
    { period: "FY2025", value: 4.12 },
  ],
  forward: [
    fp({ kind: "company_guidance", period: "FY2026", vintage: "2026-08-04", low: 4.4, high: 4.6, mean: null, provider: "Company Q2 release", basis: "GAAP, diluted" }),
    consensus("FY2026", "2026-09-01", 4.38, 4.52, 4.53, 4.66, 14),
    consensus("FY2027", "2026-09-01", 4.7, 5.04, 5.02, 5.4, 11),
    consensus("FY2028", "2026-09-01", 4.9, 5.5, 5.45, 6.1, 5),
    fp({ kind: "owner_scenario", period: "FY2027", vintage: "2026-09-20", mean: 4.85, low: 4.6, high: 5.1, provider: "Owner scenario: slower Mobility", basis: "GAAP, diluted" }),
    fp({ kind: "owner_scenario", period: "FY2028", vintage: "2026-09-20", mean: 5.15, low: 4.8, high: 5.6, provider: "Owner scenario: slower Mobility", basis: "GAAP, diluted" }),
    fp({ kind: "event_calendar_estimate", period: "FY2026", vintage: "2026-09-28", mean: 4.49, provider: "Finnhub earnings calendar", basis: "Vendor-normalised", analystCount: null }),
  ],
  snapshots: [
    consensus("FY2026", "2026-03-02", 4.2, 4.31, 4.3, 4.48, 12),
    consensus("FY2026", "2026-06-01", 4.28, 4.42, 4.4, 4.58, 14),
    consensus("FY2026", "2026-09-01", 4.38, 4.52, 4.53, 4.66, 14),
    consensus("FY2027", "2026-06-01", 4.9, 5.2, 5.18, 5.55, 11),
    consensus("FY2027", "2026-09-01", 4.7, 5.04, 5.02, 5.4, 11),
    // A different basis in an early snapshot: it must never be joined to the series above.
    { ...consensus("FY2028", "2026-06-01", 5.2, 5.8, 5.8, 6.4, 5), basis: "Adjusted, diluted" },
    consensus("FY2028", "2026-09-01", 4.9, 5.5, 5.45, 6.1, 5),
  ],
  missing: [
    { period: "FY2029", reason: "No published consensus. Finnhub forward estimates and FMP analyst estimates return a premium-tier denial.", state: "paid_only" },
  ],
};

export const EARNINGS_FIXTURE: EarningsEvent[] = [
  {
    metric: "Diluted EPS",
    period: "Q2 FY2026",
    actual: 1.42,
    consensusLow: 1.28,
    consensusMean: 1.35,
    consensusHigh: 1.41,
    analystCount: 14,
    currency: "USD",
    reportedAt: "2026-08-04",
    provider: "Company-published consensus file (IR)",
  },
  {
    metric: "Revenue (USD m)",
    period: "Q2 FY2026",
    actual: 6980,
    consensusLow: null,
    consensusMean: null,
    consensusHigh: null,
    analystCount: null,
    currency: "USD",
    reportedAt: "2026-08-04",
    provider: null,
  },
];

const DEF = {
  roic: "NOPAT ÷ average invested capital (v2, harmonised)",
  growth: "Reported revenue growth, fiscal year on fiscal year",
  margin: "Operating income ÷ revenue, as reported",
  fcf: "Free cash flow ÷ net income (v1)",
  leverage: "Net debt ÷ EBITDA (v1)",
};

const m = (value: number | null, definition: string) => ({ value, definition });

export const PEERS_FIXTURE: PeerSet = {
  definitions: {
    roic: { label: "Return on invested capital", definition: DEF.roic, unit: "percent" },
    revenue_growth: { label: "Revenue growth", definition: DEF.growth, unit: "percent" },
    operating_margin: { label: "Operating margin", definition: DEF.margin, unit: "percent" },
    fcf_conversion: { label: "Free-cash-flow conversion", definition: DEF.fcf, unit: "ratio" },
    leverage: { label: "Net debt to EBITDA", definition: DEF.leverage, unit: "ratio" },
  },
  subject: {
    name: "Fixture Industrials",
    ticker: "FIXI",
    basis: "US GAAP",
    period: "FY2025",
    why: "Subject",
    metrics: {
      roic: m(0.118, DEF.roic),
      revenue_growth: m(0.066, DEF.growth),
      operating_margin: m(0.153, DEF.margin),
      fcf_conversion: m(0.82, DEF.fcf),
      leverage: m(1.1, DEF.leverage),
    },
  },
  peers: [
    {
      name: "Alder Controls",
      ticker: "ALDC",
      basis: "US GAAP",
      period: "FY2025",
      why: "Same business line (automation), similar capitalisation",
      metrics: { roic: m(0.141, DEF.roic), revenue_growth: m(0.081, DEF.growth), operating_margin: m(0.184, DEF.margin), fcf_conversion: m(0.91, DEF.fcf), leverage: m(0.6, DEF.leverage) },
    },
    {
      name: "Brenner Drives (IFRS)",
      ticker: "BRND",
      basis: "IFRS",
      period: "FY2025",
      why: "Validated competitor, euro-area",
      metrics: { roic: m(0.102, DEF.roic), revenue_growth: m(0.043, DEF.growth), operating_margin: m(0.121, DEF.margin), fcf_conversion: m(0.74, DEF.fcf), leverage: m(1.9, DEF.leverage) },
    },
    {
      name: "Corvid Power",
      ticker: "CRVP",
      basis: "US GAAP",
      period: "FY2025",
      why: "Same business line (power systems)",
      metrics: { roic: m(0.09, "NOPAT ÷ ending invested capital (v1, legacy)"), revenue_growth: m(0.052, DEF.growth), operating_margin: m(0.117, DEF.margin), fcf_conversion: m(0.66, DEF.fcf), leverage: m(2.3, DEF.leverage) },
    },
    {
      name: "Dunmore Mobility",
      ticker: "DNMR",
      basis: "US GAAP",
      period: "FY2024",
      why: "Mobility segment peer; fiscal year ends in June, latest filing is FY2024",
      metrics: { roic: m(0.087, DEF.roic), revenue_growth: m(0.031, DEF.growth), operating_margin: m(0.094, DEF.margin), fcf_conversion: m(0.58, DEF.fcf), leverage: m(2.8, DEF.leverage) },
    },
    {
      name: "Eskerfield Systems",
      ticker: "ESKF",
      basis: "US GAAP",
      period: "FY2025",
      why: "Validated competitor",
      metrics: { roic: m(null, DEF.roic), revenue_growth: m(0.093, DEF.growth), operating_margin: m(0.168, DEF.margin), fcf_conversion: m(null, DEF.fcf), leverage: m(0.9, DEF.leverage) },
    },
  ],
};
