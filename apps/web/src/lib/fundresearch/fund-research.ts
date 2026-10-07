import type { components } from "@/lib/api/schema";
import { toNumber, type Num } from "@/lib/company/company";

type Schemas = components["schemas"];
export type FundProfile = Schemas["FundProfile"];
export type ProfileField = Schemas["ProfileField"];
export type HoldingsView = Schemas["HoldingsView"];
export type FundBucket = Schemas["FundBucket"];
export type StyleView = Schemas["StyleView"];
export type PerformanceView = Schemas["PerformanceView"];
export type PeriodReturn = Schemas["PeriodReturn"];
export type BondView = Schemas["BondView"];
export type Checklist = Schemas["Checklist"];
export type Criterion = Schemas["Criterion"];
export type FundComparison = Schemas["FundComparison"];
export type CompareFund = Schemas["CompareFund"];
export type CatalogRow = Schemas["CatalogRow"];
export type FundFact = Schemas["Fact"];
export type RateBucket = Schemas["RateBucket"];
export type NavView =Schemas["NavView"];
export type NavRow = Schemas["NavRow"];
export type SavedComparison = Schemas["SavedComparison"];

export const FUND_TABS = [
  { slug: "", label: "Profile" },
  { slug: "holdings", label: "Holdings" },
  { slug: "style", label: "Concentration and style" },
  { slug: "performance", label: "Performance" },
  { slug: "bond", label: "Bond characteristics" },
  { slug: "checklist", label: "Due diligence" },
] as const;

export function fundHref(isin: string, slug = ""): string {
  const base = `/funds/${encodeURIComponent(isin)}`;
  return slug ? `${base}/${slug}` : base;
}

export function compareHref(isins: readonly string[]): string {
  return `/funds/compare?isins=${isins.map(encodeURIComponent).join(",")}`;
}

/** A fraction as a percent with the given decimals; unknown stays null, never "0%". */
export function pct(v: Num, digits = 2): string | null {
  const n = toNumber(v);
  return n === null ? null : `${(n * 100).toFixed(digits)}%`;
}

export function signedPct(v: Num, digits = 2): string | null {
  const n = toNumber(v);
  if (n === null) return null;
  return `${n > 0 ? "+" : ""}${(n * 100).toFixed(digits)}%`;
}

export function money(v: Num, currency?: string | null): string | null {
  const n = toNumber(v);
  if (n === null) return null;
  const abs = Math.abs(n);
  const text = abs >= 1e9 ? `${(n / 1e9).toFixed(2)}bn` : abs >= 1e6 ? `${(n / 1e6).toFixed(1)}m` : n.toLocaleString("en-GB", { maximumFractionDigits: 0 });
  return currency ? `${text} ${currency}` : text;
}

export const STATUS_LABEL: Record<string, string> = { good: "Good", watch: "Watch", concern: "Concern", info: "For your review", unknown: "Unknown" };

/** Order for the checklist: problems first, then what is unknown, then the rest. */
export const STATUS_RANK: Record<string, number> = { concern: 0, watch: 1, unknown: 2, info: 3, good: 4 };

export const FACT_LABEL: Record<string, string> = {
  replication_method: "Replication method",
  securities_lending: "Securities lending",
  lending_revenue_share: "Lending revenue share to the fund",
  inception_date: "Inception date",
  domicile: "Domicile",
  distribution_tax_note: "Distribution and tax note",
  liquidity_note: "Liquidity note",
  tracking_index: "Tracking index",
  ucits: "UCITS status",
  benchmark_symbol: "Benchmark index symbol",
  benchmark_currency: "Benchmark currency",
  benchmark_basis: "Benchmark return basis",
  average_maturity_years: "Average maturity (years)",
  effective_duration_years: "Effective duration (years)",
  credit_quality_note: "Credit quality note",
};

export const BENCHMARK_BASIS_OPTIONS = [
  ["price", "Price index (without distributions)"],
  ["total", "Total-return index (distributions reinvested)"],
] as const;

const NON_NEGATIVE_NUMBER_FACTS = new Set(["average_maturity_years", "effective_duration_years"]);

/** True for the facts whose value must be a non-negative number. */
export function isNumericFact(key: string): boolean {
  return NON_NEGATIVE_NUMBER_FACTS.has(key);
}

/**
 * Checks a fact value the way the server does and returns the cleaned value, or an error message.
 * Currency codes are upper-cased; numbers must be non-negative and finite.
 */
export function checkFactValue(key: string, raw: string): { value: string } | { error: string } {
  const text = raw.trim();
  if (!text) return { error: "Enter the value." };
  if (isNumericFact(key)) {
    const n = Number(text);
    if (!/^\d+(\.\d+)?$/.test(text) || !Number.isFinite(n)) return { error: `${FACT_LABEL[key]} must be a number of zero or more, for example 6.5.` };
    return { value: text };
  }
  if (key === "benchmark_currency") {
    const code = text.toUpperCase();
    return /^[A-Z]{3}$/.test(code) ? { value: code } : { error: "The benchmark currency is a three-letter code such as USD." };
  }
  if (key === "benchmark_basis") {
    return text === "price" || text === "total" ? { value: text } : { error: "Choose price or total return." };
  }
  return { value: text };
}

/** Parses pasted lines of "YYYY-MM-DD,value" into NAV points; lines that do not fit are returned as errors. */
export function parseNavLines(text: string): { rows: { as_of: string; nav: string }[]; bad: string[] } {
  const rows: { as_of: string; nav: string }[] = [];
  const bad: string[] = [];
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim();
    if (!t) continue;
    const m = /^(\d{4}-\d{2}-\d{2})\s*[,;\t ]\s*(\d+(?:\.\d+)?)$/.exec(t);
    if (!m || Number.isNaN(new Date(`${m[1]}T00:00:00Z`).getTime()) || Number(m[2]) <= 0) bad.push(t);
    else rows.push({ as_of: m[1], nav: m[2] });
  }
  return { rows, bad };
}

export const REPLICATION_OPTIONS = [
  ["physical_full", "Physical, full replication"],
  ["physical_sampled", "Physical, sampled"],
  ["synthetic", "Synthetic (swap)"],
] as const;

export function lagDays(asOf: string | null | undefined, today = new Date()): number | null {
  if (!asOf) return null;
  const d = new Date(`${asOf}T00:00:00Z`);
  return Number.isNaN(d.getTime()) ? null : Math.floor((today.getTime() - d.getTime()) / 86_400_000);
}
