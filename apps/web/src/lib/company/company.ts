import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type StatementsView = Schemas["StatementsView"];
export type StatementCell = Schemas["Cell"];
export type StatementLine = Schemas["Line"];
export type StatementColumn = Schemas["Column"];
export type RatiosView = Schemas["RatiosView"];
export type RatioRow = Schemas["RatioRow"];
export type PeerCompare = Schemas["PeerCompare"];
export type Valuation = Schemas["CompanyValuation"];
export type Assumptions = Schemas["Assumptions"];
export type ScenarioResult = Schemas["ScenarioResult"];
export type SavedScenario = Schemas["SavedScenario"];
export type Overview = Schemas["Overview"];
export type Estimates = Schemas["Estimates"];
export type Network = Schemas["Network"];
export type CapitalAllocation = Schemas["CapitalAllocation"];
export type Governance = Schemas["Governance"];
export type CaseView = Schemas["CaseWithObserved"];
export type Observed = Schemas["Observed"];
export type CaseItem = Schemas["CaseItem"];
export type CaseItemIn = Schemas["CaseItemIn"];
export type PeerGroup = Schemas["PeerGroup"];
export type Rubric = Schemas["Rubric"];
export type RubricComponent = Schemas["Component"];
export type RubricIndicator = Schemas["Indicator"];
export type SegmentAnalysis = Schemas["SegmentAnalysis"];
export type SegmentBreakdown = Schemas["Breakdown"];
export type SegmentMapIn = Schemas["MapIn"];
/** The (label, share) pairs are typed as tuples in one response and arrays in another; accept both. */
export type CompanyReport = Omit<Schemas["CompanyReport"], "segments"> & {
  segments: { breakdown: string; period: string | null; lines: readonly (readonly (string | number | null)[])[]; note?: string | null }[];
};
export type ReportVersionSummary = Schemas["ReportVersionSummary"];

export type Num = string | number | null | undefined;

/** Decimals arrive as strings or numbers; anything unparsable is unknown, never zero. */
export function toNumber(v: Num): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export const TABS = [
  { slug: "", label: "Overview" },
  { slug: "financials", label: "Financials" },
  { slug: "ratios", label: "Ratios" },
  { slug: "peers", label: "Peers" },
  { slug: "segments", label: "Segments" },
  { slug: "assessment", label: "Assessment" },
  { slug: "valuation", label: "Valuation" },
  { slug: "allocation", label: "Capital allocation" },
  { slug: "estimates", label: "Estimates" },
  { slug: "network", label: "Network and patents" },
  { slug: "governance", label: "Governance" },
  { slug: "case", label: "Investment case" },
  { slug: "report", label: "Report" },
] as const;

export function companyHref(ticker: string, slug = ""): string {
  const base = `/companies/${encodeURIComponent(ticker)}`;
  return slug ? `${base}/${slug}` : base;
}

const GROUPED = new Intl.NumberFormat("en-GB", { maximumFractionDigits: 0 });

function fixed(n: number, digits: number): string {
  return new Intl.NumberFormat("en-GB", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
}

/** One statement figure in its unit: money and share counts grouped, per-share to cents, fractions as percent. */
export function formatFigure(value: Num, unit: string): string | null {
  const n = toNumber(value);
  if (n === null) return null;
  if (unit === "fraction") return `${fixed(n * 100, 1)}%`;
  if (unit === "per_share") return fixed(n, 2);
  const abs = Math.abs(n);
  if (abs > 0 && abs < 1 && unit === "money") return fixed(n, 2);
  return GROUPED.format(n);
}

/** One ratio in its unit; `null` is unknown. */
export function formatRatio(value: Num, unit: string): string | null {
  const n = toNumber(value);
  if (n === null) return null;
  switch (unit) {
    case "fraction":
      return `${fixed(n * 100, 1)}%`;
    case "multiple":
      return `${fixed(n, 2)}×`;
    case "days":
      return `${fixed(n, 0)} d`;
    case "years":
      return `${fixed(n, 1)} yrs`;
    case "money_per_share":
      return fixed(n, 2);
    default:
      return fixed(n, 2);
  }
}

export const UNIT_LABEL: Record<string, string> = { "1": "units", "1000": "thousands", "1000000": "millions" };

/** `n/m` reasons arrive as "n/m: reason" or "missing x"; show the kind and the reason apart. */
export function splitReason(reason: string | null | undefined): { kind: "nm" | "missing" | "none"; text: string } {
  if (!reason) return { kind: "none", text: "" };
  if (reason.startsWith("n/m")) return { kind: "nm", text: reason.replace(/^n\/m:?\s*/, "") };
  return { kind: "missing", text: reason };
}

export type GridView = { rowShifts: number[]; colShifts: number[]; at: (row: number, col: number) => number | null };

/** The sensitivity cells as a growth × discount-rate matrix. */
export function gridMatrix(cells: readonly { growth_shift: Num; rate_shift: Num; value: Num }[]): GridView {
  const g = [...new Set(cells.map((c) => toNumber(c.growth_shift) ?? 0))].sort((a, b) => a - b);
  const r = [...new Set(cells.map((c) => toNumber(c.rate_shift) ?? 0))].sort((a, b) => a - b);
  const map = new Map(cells.map((c) => [`${toNumber(c.growth_shift)}|${toNumber(c.rate_shift)}`, toNumber(c.value)]));
  return { rowShifts: g, colShifts: r, at: (i, j) => map.get(`${g[i]}|${r[j]}`) ?? null };
}

/** Position of a value on a low–high range as 0..1, clamped; null when the range is degenerate. */
export function rangePosition(value: number | null, low: number | null, high: number | null): number | null {
  if (value === null || low === null || high === null || high <= low) return null;
  return Math.min(1, Math.max(0, (value - low) / (high - low)));
}

/** Percent typed by the owner → fraction (and back), so assumptions are edited the way they are read. */
export function percentToFraction(text: string): number | null {
  const n = Number(text.trim().replace(",", ".").replace(/%$/, ""));
  return text.trim() === "" || !Number.isFinite(n) ? null : n / 100;
}

export function fractionToPercent(v: Num, digits = 2): string {
  const n = toNumber(v);
  return n === null ? "" : String(Number((n * 100).toFixed(digits)));
}

/** "Top quarter", "Median", … for a 0..1 percentile among peers; says nothing for null. */
export function percentileLabel(p: Num): string | null {
  const n = toNumber(p);
  if (n === null) return null;
  const pct = Math.round(n * 100);
  return `${pct}th percentile`;
}

export const SCENARIO_ORDER = ["bear", "base", "bull"];

export function sortScenarios<T extends { name: string }>(items: readonly T[]): T[] {
  const rank = (name: string) => {
    const i = SCENARIO_ORDER.indexOf(name);
    return i === -1 ? SCENARIO_ORDER.length : i;
  };
  return [...items].sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name));
}

/** The statement line keys a reader wants first on the financials page. */
export const HEADLINE_KEYS = new Set(["revenue", "gross_profit", "operating_income", "net_income", "total_assets", "total_liabilities", "cash_from_operations", "free_cash_flow"]);
