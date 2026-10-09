import type { components } from "@/lib/api/schema";
import { formatPercent } from "@/lib/format";

type S = components["schemas"];
export type ExAnteRisk = S["ExAnteRisk"];
export type PositionExAnte = S["PositionExAnte"];
export type GroupRisk = S["GroupRisk"];
export type TierCheck = S["TierCheck"];

/** How many times its size a holding's share of risk is: 2 means it carries twice the risk its weight suggests. */
export function riskMultiple(weightOfInvested: number, riskShare: number): number | null {
  if (!(weightOfInvested > 0) || !Number.isFinite(riskShare)) return null;
  return riskShare / weightOfInvested;
}

/** Largest value among the bars that share one scale, never zero so widths stay finite. */
export function sharedMax(values: readonly number[]): number {
  const m = Math.max(0, ...values.filter(Number.isFinite));
  return m > 0 ? m : 1;
}

/** A bar width as a percentage of the track, clamped to 0 to 100. */
export function barWidth(value: number, max: number): number {
  if (!(max > 0) || !Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, (value / max) * 100));
}

/** One sentence on how much of the book the model could see. */
export function coverageSentence(c: ExAnteRisk["coverage"]): string | null {
  if (!c) return null;
  const pct = (x: number) => formatPercent(x, { decimals: 0 });
  const parts = [`${pct(c.real_weight)} of the portfolio has its own history`];
  if (c.proxied_weight > 0.0005) parts.push(`${pct(c.proxied_weight)} is backfilled from a similar ETF`);
  if (c.uncovered_weight > 0.0005) parts.push(`${pct(c.uncovered_weight)} is left out for lack of history`);
  return parts.join(", ") + ".";
}

/** The risk position of a group against its budget: "over", "within" or "unbudgeted". */
export function budgetStatus(share: number, budget: number | undefined): "over" | "within" | "unbudgeted" {
  if (budget === undefined) return "unbudgeted";
  return share > budget ? "over" : "within";
}
