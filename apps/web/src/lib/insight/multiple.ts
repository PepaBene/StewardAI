/*
 * The multiple-path scenario editor's draft (insight I6.6): every input as the text the owner
 * typed, per-year paths either one constant value or one value per year. Conversion to the API's
 * assumptions reports the first field that is not a number instead of guessing.
 */
import type { components } from "@/lib/api/schema";
import { fractionToPercent, percentToFraction } from "@/lib/company/company";
import { normaliseSeparators } from "@/lib/number-input";

import { num } from "./insight";

export type MultipleAssumptions = components["schemas"]["MultipleAssumptions"];
export type MultipleValuation = components["schemas"]["MultipleValuation"];
export type SavedMultiple = components["schemas"]["SavedMultiple"];
export type MultipleDrift = components["schemas"]["Drift"];

export type PathKey = "growth" | "margin" | "share";

export type MultipleDraft = {
  name: string;
  years: string;
  growth: string[];
  margin: string[];
  share: string[];
  exitPe: string;
  riskFree: string;
  erp: string;
  beta: string;
  extra: string;
  discountRate: string;
  baseRevenue: string;
};

const ORDER = ["bear", "base", "bull"];

export function sortMultiple<T extends { name: string }>(items: readonly T[]): T[] {
  return [...items].sort((a, b) => (ORDER.indexOf(a.name) + 1 || 99) - (ORDER.indexOf(b.name) + 1 || 99));
}

export function toMultipleDraft(a: MultipleAssumptions): MultipleDraft {
  const pct = (xs: readonly (number | string)[]) => xs.map((x) => fractionToPercent(x));
  return {
    name: a.name,
    years: String(a.years ?? 5),
    growth: pct(a.revenue_growth),
    margin: pct(a.net_margin),
    share: pct(a.share_change ?? [0]),
    exitPe: String(num(a.exit_pe) ?? ""),
    riskFree: fractionToPercent(a.risk_free ?? 0.04),
    erp: fractionToPercent(a.equity_risk_premium ?? 0.05),
    beta: String(num(a.beta) ?? 1),
    extra: fractionToPercent(a.extra_premium ?? 0),
    discountRate: a.discount_rate === null || a.discount_rate === undefined ? "" : fractionToPercent(a.discount_rate),
    baseRevenue: a.base_revenue === null || a.base_revenue === undefined ? "" : String(a.base_revenue),
  };
}

/** Switch a path between one constant value and one value per year, keeping what was typed. */
export function setPerYear(values: readonly string[], perYear: boolean, years: number): string[] {
  if (!perYear) return [values[0] ?? ""];
  return Array.from({ length: years }, (_, i) => values[i] ?? values[values.length - 1] ?? "");
}

/** A changed horizon resizes the per-year paths; constant paths stay one value. */
export function withYears(d: MultipleDraft, years: number): MultipleDraft {
  const resize = (xs: string[]) => (xs.length > 1 ? setPerYear(xs, true, years) : xs);
  return { ...d, years: String(years), growth: resize(d.growth), margin: resize(d.margin), share: resize(d.share) };
}

function number(text: string): number | null {
  const n = Number(normaliseSeparators(text));
  return text.trim() === "" || !Number.isFinite(n) ? null : n;
}

export function fromMultipleDraft(d: MultipleDraft): MultipleAssumptions | string {
  const years = number(d.years);
  if (years === null || !Number.isInteger(years) || years < 3 || years > 10) return `${d.name}: the horizon must be 3 to 10 years.`;
  const paths: Record<PathKey, number[]> = { growth: [], margin: [], share: [] };
  const labels: Record<PathKey, string> = { growth: "revenue growth", margin: "net margin", share: "share change" };
  for (const k of ["growth", "margin", "share"] as const) {
    const xs = d[k].map(percentToFraction);
    if (xs.some((x) => x === null)) return `${d.name}: every ${labels[k]} value needs a number.`;
    if (xs.length !== 1 && xs.length !== years) return `${d.name}: ${labels[k]} needs one value or one per year.`;
    paths[k] = xs as number[];
  }
  const pe = number(d.exitPe);
  if (pe === null || pe <= 0) return `${d.name}: the exit P/E must be a positive number.`;
  const rf = percentToFraction(d.riskFree);
  const erp = percentToFraction(d.erp);
  const extra = percentToFraction(d.extra);
  const beta = number(d.beta);
  if (rf === null || erp === null || extra === null || beta === null) return `${d.name}: risk-free rate, equity risk premium, extra premium and beta must be numbers.`;
  const override = d.discountRate.trim() === "" ? null : percentToFraction(d.discountRate);
  if (d.discountRate.trim() !== "" && override === null) return `${d.name}: the discount-rate override is not a number.`;
  const revenue = d.baseRevenue.trim() === "" ? null : number(d.baseRevenue);
  if (d.baseRevenue.trim() !== "" && (revenue === null || revenue <= 0)) return `${d.name}: starting revenue must be a positive number.`;
  return {
    name: d.name,
    years,
    base_revenue: revenue,
    revenue_growth: paths.growth,
    net_margin: paths.margin,
    exit_pe: pe,
    share_change: paths.share,
    diluted_shares: null,
    risk_free: rf,
    equity_risk_premium: erp,
    beta,
    extra_premium: extra,
    discount_rate: override,
  };
}

/** Earnings (or revenue) path for the projection chart: filed years, then each scenario's years. */
export function projection(v: MultipleValuation, measure: "earnings" | "revenue") {
  const past = v.history
    .map((h) => ({ year: h.year, value: num(measure === "earnings" ? h.net_income : h.revenue) }))
    .filter((p): p is { year: number; value: number } => p.value !== null);
  // Projection years are counted from the starting figure (1..horizon); place them after the last
  // filed year so past and forecast share one calendar axis.
  const start = past.length ? past[past.length - 1].year : new Date().getFullYear();
  const scenarios = v.scenarios.map((s) => ({
    name: s.name,
    selected: s.selected,
    points: s.years.map((y) => ({ year: start + y.year, value: num(measure === "earnings" ? y.earnings : y.revenue) ?? 0 })),
  }));
  return { past, scenarios };
}
