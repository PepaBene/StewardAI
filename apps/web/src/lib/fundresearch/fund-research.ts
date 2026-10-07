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
};

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
