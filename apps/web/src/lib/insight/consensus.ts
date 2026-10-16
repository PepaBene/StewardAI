/*
 * Analyst consensus chart data (insight I8.3): price, the consensus mean as a step line, the
 * high-low band, and a cone from the last close to the 12-month target range.
 */
import type { components } from "@/lib/api/schema";

import { num } from "./insight";

export type ConsensusView = components["schemas"]["ConsensusView"];
export type PortfolioConsensus = components["schemas"]["PortfolioConsensus"];
export type ConsensusPoint = ConsensusView["history"][number];

export const COVERAGE_LABEL = { complete: "Complete", partial: "Partial", unavailable: "Unavailable" } as const;

export type Cone = { from: { t: number; v: number }; to: { t: number; mean: number; high: number | null; low: number | null } };

const DAY = 86_400_000;

/** From the last stored close to the latest target 12 months after the snapshot; null without both. */
export function cone(prices: readonly { date: string; close: number | string }[], latest: ConsensusPoint | null | undefined): Cone | null {
  const last = prices[prices.length - 1];
  const mean = num(latest?.mean ?? null);
  if (!last || !latest || mean === null) return null;
  const start = new Date(last.date).getTime();
  const end = new Date(latest.as_of).getTime() + 365 * DAY;
  if (end <= start) return null;
  return {
    from: { t: start, v: Number(last.close) },
    to: { t: end, mean, high: num(latest.high ?? null), low: num(latest.low ?? null) },
  };
}

/** The snapshot in force on a date (the latest at or before it). */
export function pointAt(history: readonly ConsensusPoint[], t: number): ConsensusPoint | null {
  let best: ConsensusPoint | null = null;
  for (const h of history) if (new Date(h.as_of).getTime() <= t) best = h;
  return best;
}
