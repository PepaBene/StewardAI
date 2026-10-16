/*
 * View helpers for the Income screen (insight plan I2): money in the account currency, the
 * calendar's event types, and tier rows. Figures come from `/portfolio/dividends`; nothing here
 * recomputes a total.
 */
import type { components } from "@/lib/api/schema";
import { formatMoney, formatPercent } from "@/lib/format";

import { num, type Num } from "./insight";

type Schemas = components["schemas"];
export type PortfolioDividends = Schemas["PortfolioDividends"];
export type DividendCalendar = Schemas["DividendCalendar"];
export type CompanyDividends = Schemas["CompanyDividends"];
export type HoldingIncome = Schemas["HoldingIncome"];
export type PaymentLine = Schemas["PaymentLine"];
export type TierLine = Schemas["IncomeTier"];

export function money(v: Num, currency: string | null | undefined, decimals = 2): string {
  const n = num(v);
  if (n === null) return "—";
  return currency ? formatMoney(n, currency, { decimals }) : n.toFixed(decimals);
}

export function pct(v: Num, decimals = 1, signed = false): string {
  return formatPercent(num(v), { decimals, signed });
}

/** Calendar event types: one dot style each; estimated ones are hollow, unconfirmed dashed-grey. */
export const CALENDAR_KINDS = [
  { key: "ex_dividend", label: "Ex-dividend", dot: "bg-chart-line" },
  { key: "estimated_ex_dividend", label: "Estimated ex-dividend", dot: "border-chart-forecast", hollow: true },
  { key: "payment", label: "Payment", dot: "bg-gain" },
  { key: "estimated_payment", label: "Estimated payment", dot: "border-gain", hollow: true },
  { key: "unconfirmed_ex_dividend", label: "Unconfirmed ex-dividend", dot: "border-text-faint", hollow: true },
  { key: "unconfirmed_payment", label: "Unconfirmed payment", dot: "bg-text-faint" },
  { key: "earnings", label: "Earnings date", dot: "bg-warning-signal" },
] as const;

export const KIND_LABEL: Record<PaymentLine["kind"], string> = {
  received: "Received",
  declared: "Declared",
  estimated: "Estimated",
  unconfirmed: "Awaiting confirmation",
};

export const TIER_LABEL: Record<TierLine["tier"], string> = {
  high: "High quality",
  medium: "Medium quality",
  low: "Low quality",
  unscored: "Not scored",
};

export const TIER_BG: Record<TierLine["tier"], string> = {
  high: "bg-tier-high",
  medium: "bg-tier-medium",
  low: "bg-tier-low",
  unscored: "bg-text-faint",
};

/** Tiers in reading order, high first, so the stacked share bar reads left to right. */
export function orderedTiers(tiers: readonly TierLine[]): TierLine[] {
  const order: TierLine["tier"][] = ["high", "medium", "low", "unscored"];
  return order.map((t) => tiers.find((x) => x.tier === t)).filter((x): x is TierLine => !!x);
}
